#
# mock-server.py: A mitmproxy script for mocking server responses.
# The mock configuration is loaded from a JSON file, e.g.:
#
#   mitmdump -s mock-server.py --set mock=example.json -m reverse:https://foo.com/
#
# Documentation is a work in progress, see example.json for examples or ask
# the author (Kimmo Kulovesi) for now.
#

import json
import random
import re
from collections import OrderedDict
from typing import Optional
from mitmproxy import http
from mitmproxy import ctx
from mitmproxy import net

def host_matches(host: str, allow) -> bool:
    """
    Return whether `host` matches `allow`.

    `allow` may be a string pattern, or a list of such patterns, in which
    case returns True if `host` matches any pattern in `allow`.

    - If the pattern begins with a dot, `host` must end with the suffix
      following the dot
    - If the patterns ends with a dot, `host` must start with the pattern
    - Otherwise `host` must equal the pattern
    """
    if isinstance(allow, str):
        if allow.startswith("."):
            return host.endswith(allow[1:])
        elif allow.endswith("."):
            return host.startswith(allow)
        else:
            return host == allow
    elif isinstance(allow, dict):
        if host in allow:
            return True
    else:
        for allowed_host in allow:
            if host_matches(host, allowed_host):
                return True
    return False

def matches_value_or_list(value, allow) -> bool:
    """
    Return whether `value` matches `allow`.

    `allow` may either be of the same type as `value`, or a list of such items,
    in which case returns True if `value` matches any element of `allow`. In
    case of strings, value may have a tilde prefix (`~`) in which case its
    suffix is treated as a regular expression.
    """
    if type(value) is type(allow):
        if isinstance(allow, str) and allow.startswith("~"):
            allow_re = re.compile(allow[1:], re.X)
            return bool(allow_re.search(value)) or value == allow
        else:
            return value == allow
    else:
        for allowed in allow:
            if matches_value_or_list(str, allowed):
                return True
    return False

def request_matches_config(request: http.HTTPRequest, config: dict) -> bool:
    """
    Returns whether `request` is matched by `config`. This checks the following:

    - `host` (some patterns supported, see `host_matches`)
    - `scheme` (exact match or list)
    - `path` (exact match or list, normally matched already before coming here)
    - `query` (keys are exact, values either exact or list)
    """
    if not config:
        return False
    host = request.host
    whitelist = config.get("host", mock_config.get("host"))
    if (whitelist is not None) and not host_matches(host, whitelist):
        return False
    required_scheme = config.get("scheme", mock_config.get("scheme"))
    if required_scheme and not matches_value_or_list(request.scheme, required_scheme):
        return False
    required_path = config.get("path")
    if required_path and not matches_value_or_list(request.path, required_path):
        return False
    required_query = config.get("query")
    if required_query:
        query = request.query
        for key in required_query:
            value = required_query[key]
            if not ((key in query) and matches_value_or_list(query[key], value)):
                return False
    return True

def is_subset(subset, superset) -> bool:
    """
    Return whether `subset` is indeed a subset of `superset`. That is, all
    items contained in `subset` must be found exactly in `superset`. Any strings
    in subset may be prefixed with a tilde (`~`) in which case the suffix is
    interpreted as a regular expression.
    """
    try:
        if isinstance(subset, dict):
            return all(key in superset and is_subset(subset[key], superset[key]) for key in subset)
        elif isinstance(subset, list):
            return all(any(is_subset(subitem, superitem) for superitem in superset) for subitem in subset)
        elif isinstance(subset, str):
            if subset == "~":
                return True
            elif subset.startswith("~"):
                allow_re = re.compile(subset[1:], re.M | re.X | re.S)
                return bool(allow_re.search(str(superset)))
            else:
                return str(superset) == subset
        else:
            return subset == superset
    except Exception as error:
        ctx.log.debug("is_subset incompatible types: {}: {} {}".format(error, subset, superset))
        return False

def content_matches(content: str, allow) -> bool:
    """
    Returns whether `content` matches the `allow` criteria.

    `allow` may be of the following types:
    - a string prefixed with a tilde (`~`), in which case the suffix is
      interpreted as a regular expression and matched against `content`
    - any other string, which needs to be a substring of `content`
    - a dictionary, in which case `content` is interpreted as a JSON object
      which must be a superset `allow` (see `is_subset`)
    - a list of any of the above, which must all match
    """
    if isinstance(allow, str):
        if allow.startswith("~"):
            allow_re = re.compile(allow[1:], re.X)
            return bool(allow_re.search(content))
        else:
            return allow in content
    elif isinstance(allow, dict):
        try:
            content_object = json.loads(content)
            return is_subset(allow, content_object)
        except:
            return False
    else:
        for allowed in allow:
            if not content_matches(content, allowed):
                return False
    return True

def response_matches_config(response: http.HTTPResponse, config: dict) -> bool:
    """
    Returns whether `response` is matched by `config`. This checks the following:

    - `status` (the HTTP status code)
    - `content` (a string or a list of strings where _all_ must match)

    For content matching, each string can either be a regular expression denoted
    by a tilde prefix (`~`), otherwise a substring that must be found exactly.
    """
    required_status = config.get("status")
    if required_status and not matches_value_or_list(response.status_code, required_status):
        return False
    required_content = config.get("content")
    if required_content and not content_matches(response.text, required_content):
        return False
    return True

def merge_content(merge, content):
    """
    Merges `merge` into `content` recursively for dictionaries and lists.
    """
    if isinstance(merge, str) and (merge.startswith(".") and (merge.endswith(".json") or merge.endswith(".js"))):
        try:
            with open(merge) as merge_file:
                merge = json.load(merge_file)
        except:
            pass
    if isinstance(merge, dict):
        if isinstance(content, dict):
            for key in merge:
                content[key] = merge_content(merge[key], content.get(key))
        elif isinstance(content, list) and ("where" in merge):
            where = merge["where"]
            for index, element in enumerate(content):
                if is_subset(where, element):
                    if "merge" in merge:
                        content[index] = merge_content(merge["merge"], element)
                    elif "replace" in merge:
                        content[index] = merge_content(merge["replace"], None)
        else:
            content = merge
    elif isinstance(merge, list):
        if isinstance(content, list):
            content = content + merge
        elif content is None:
            content = merge
        else:
            content = [ content ] + merge
    else:
        content = merge
    return content

def delete_content(delete, content):
    """
    Returns `content` after recursively deleting `delete` from it.

    Any matching dictionary keys are deleted if their value is empty or matches
    the corresponding value in `content`. For lists, if `delete` has a non-empty
    list, its elements are compared to the corresponding list in `content`
    according to `is_subset`, and any matches are deleted from `content`.
    """
    if isinstance(delete, dict):
        for key in delete:
            value = delete[key]
            if isinstance(value, dict):
                if value:
                    content_value = content.get(key)
                    if isinstance(content_value, dict):
                        new_content = delete_content(value, content_value)
                        content[key] = new_content
                else:
                    content.pop(key, None)
            elif isinstance(value, list):
                if value:
                    content_value = content.get(key)
                    if isinstance(content_value, list):
                        content[key] = delete_content(value, content_value)
                else:
                    content.pop(key, None)
            else:
                if (not value) or content.get(key) == value:
                    content.pop(key, None)
    elif isinstance(delete, list):
        if delete and isinstance(content, list):
            for deletion in delete:
                content = [ value for value in content if not is_subset(deletion, value) ]
        else:
            content = []
    return content

def count_based_config(path, config: dict) -> dict:
    """
    Return a configuration dict from `config["count"]` based on the number of
    times `path` has been hit.

    The configuration dictionary is formed from `config["count"]` keys applied
    on top of one another in the following order:
    - `*`: applied to every count
    - `odd` or `even`: applied to alternating counts
    - `1`, `2`, `3`, …: applied to exact counts, starting from 1

    Note that the keys of `config["count"]` are strings, even for the numeric
    counts, since the data is loaded from JSON.
    """
    result = {}
    count_config = config.get("count")
    if (not count_config) and ("once" in config):
        count_config = { "1": config["once"] }
    if count_config:
        count_id = count_config.get("id", path)
        count = hit_count.get(count_id, 0) + 1
        hit_count[count_id] = count
        result.update(count_config.get("*", {}))
        result.update(count_config.get("even" if (count % 2) == 0 else "odd", {}))
        result.update(count_config.get(str(count), {}))
    return result

def encode_content(content) -> (bytes, str):
    """
    Return a tuple of `content` encoded into bytes, and a guess of its type.

    `content` may be any of the following:
    - A file name string, in which case the content is loaded from the file and
      type inferred from the file's extension (json, js, html, xml, txt, md).
    - A raw string, in which case it is encoded according as UTF-8, and the type
      is inferred to be HTML if it starts with a `<`, otherwise JSON.
    - An object (such as a dictionary) that can be dumped as JSON, in which case
      it is converted to JSON.
    """
    content_type = "application/json"
    if isinstance(content, str):
        try:
            with open(content, "rb") as content_file:
                if content.endswith("html"):
                    content_type = "text/html"
                elif content.endswith(".xml"):
                    content_type = "text/xml"
                elif content.endswith(".txt") or content.endswith(".md"):
                    content_type = "text/plain"
                elif content.endswith(".js"):
                    content_type = "application/javascript"
                return content_file.read(), content_type
        except FileNotFoundError:
            if content.startswith("<"):
                content_type = "text/html"
            return content.encode("utf-8"), content_type + "; charset=utf-8"
    else:
        return json.dumps(content).encode("utf-8"), content_type + "; charset=utf-8"

def make_response(response, status, content, headers) -> http.HTTPResponse:
    """
    Return a new `HTTPResponse` object constructed from the configuration
    `response`, with the status code, content and headers defaulting to the
    given values unless overridden by `response`.

    `response` may contain any combination of:
     - `content`: A file name, a raw string, or a JSON object
     - `status`: The HTTP status code
     - `type`: The Content-Type header type with or without the charset
     - `charset`: The charset part of the Content-Type header
     - `headers`: A dictionary of HTTP headers

    See also: `encode_content`
    """
    if isinstance(response, str):
        response = { "content": response }
    content, content_type = encode_content(response.get("content", content))
    content_type = response.get("type", headers.get("Content-Type", content_type))
    charset = response.get("charset", mock_config.get("charset", "utf-8"))
    if charset and not ((";" in content_type) or ("image" in content_type)):
        content_type = "{}; charset={}".format(content_type, charset)
    headers = {**headers, **{ "Content-Type": content_type }}
    headers.update(response.get("headers", {}))
    status = response.get("status", status)
    ctx.log.debug("Response {}: headers={} content={}".format(status, headers, content))
    return http.HTTPResponse.wrap(
        net.http.Response.make(status, content, headers),
    )

# Called when the script is loaded, registers command-line options.
def load(script) -> None:
    script.add_option("mock", str, "mock.json", "Mock configuration JSON file")

def extract_regex_paths(config: OrderedDict) -> OrderedDict:
    re_paths = OrderedDict()
    if config:
        for path, handler in config.items():
            if not path.startswith("~"):
                continue
            try:
                path_re = re.compile(path[1:], re.X)
                re_paths[path_re] = json.loads(json.dumps(handler))
            except Exception as error:
                ctx.log.error("Error: regex path {}: {}".format(path, error))
    return re_paths

# Called to configure the script
def configure(updated) -> None:
    global mock_config, re_request, re_response
    global hit_count, cycle_index
    if "mock" in updated:
        mock_filename = ctx.options.mock
        with open(mock_filename) as mock_config_file:
            try:
                # OrderedDict hack to preserve path regex order
                ordered_config = json.load(mock_config_file, object_pairs_hook=OrderedDict)
                mock_config = json.loads(json.dumps(ordered_config))
                hit_count.clear()
                cycle_index.clear()
                re_request = extract_regex_paths(ordered_config.get("request"))
                re_response = extract_regex_paths(ordered_config.get("response"))
                ctx.log.info("Mock configuration: {}".format(mock_filename))
            except Exception as error:
                ctx.log.error("Error: {}: {}".format(mock_filename, error))
        if not mock_config:
            ctx.log.error("No configuration: use --set config.json")
            return

def resolve_config(flow: http.HTTPFlow, event: str) -> Optional[dict]:
    """
    Returns configuration for the event (`request` or `response`) from
    the flow state and the global mock configuration, or `None` if no
    configuration item matches the event.
    """
    is_request = (event == "request")
    path = flow.request.path.split("?")[0]
    handlers = mock_config.get(event, {})
    # TODO: Allow global configs to be merged deeper (e.g., global modify)
    path_handler = handlers.get(flow.request.path, handlers.get(path))
    if path_handler is None:
        # TODO: Define order for regexes
        # Iterate over the regexes if there is no direct match
        re_handlers = (re_request if is_request else re_response)
        for path_re in re_handlers:
            if path_re.search(flow.request.path):
                path_handler = re_handlers[path_re]
                break
        if path_handler is None:
            return None
    config = handlers.get("*", {})
    if isinstance(path_handler, list):
        matched = None
        for handler in path_handler:
            if isinstance(config, list):
                handler_config = {**handler}
            else:
                handler_config = {**config, **handler}
            if request_matches_config(flow.request, handler_config) and (is_request or response_matches_config(flow.response, handler_config)):
                matched = handler_config
                break
        if matched is None:
            return None
        config = matched
    else:
        if isinstance(config, list):
            for handler in config:
                handler_config = {**handler, **path_handler}
                if request_matches_config(flow.request, handler_config) and (is_request or response_matches_config(flow.response, handler_config)):
                    config = handler_config
                    break
        else:
            config = {**config, **path_handler}
        if not (request_matches_config(flow.request, config) and (is_request or response_matches_config(flow.response, config))):
            return None
    # TODO: Allow recursion to arbitrary depth
    config.update(count_based_config(path, config))
    random_configs = config.get("random")
    if random_configs:
        config.update(random.choice(random_configs))
    cycle = config.get("cycle", config.get("round"))
    if cycle:
        cycle_id = config.get("cycle-id", path)
        index = cycle_index.get(cycle_id, 0)
        cycle_index[cycle_id] = ((index + 1) % len(cycle))
        config.update(cycle[index])
    if config.get("pass", False):
        return None
    return config

# Called for every incoming request, before passing anything to the remote
# server. Handling requests allows mocking data for endpoints not present
# on remote, or modifying the outgoing request.
def request(flow: http.HTTPFlow) -> None:
    ctx.log.debug("Request {}: {}".format(flow.request.path, flow.request))
    config = resolve_config(flow, "request")
    if config is None:
        return
    ctx.log.debug("Match request: {}".format(flow.request.path))
    modify = config.get("modify")
    if modify:
        # TODO: Allow regex replacement for modify
        ctx.log.info("Modify request: {} -> {}".format(flow.request, modify))
        flow.request.scheme = modify.get("scheme", flow.request.scheme)
        flow.request.host = modify.get("host", flow.request.host)
        flow.request.path = modify.get("path", flow.request.path)
        flow.request.query = {**(flow.request.query or {}), **modify.get("query", {})}
        content = modify.get("content")
        if content:
            content, _ = encode_content(content)
            flow.request.content = content
        flow.request.headers.update(modify.get("headers", {}))
    response = config.get("response")
    if response:
        flow.response = make_response(response, 200, "", {})
        ctx.log.info("Mock {}".format(flow.request.path))

# Called before returning a response from the remote server. This can be
# used to rewrite responses based on their original contents. For example,
# the same endpoint may return multiple types of responses and we may wish
# to mock only some of them, or to mock them in different ways.
def response(flow: http.HTTPFlow) -> None:
    ctx.log.debug("Response {}: {}".format(flow.request.path, flow.response))
    config = resolve_config(flow, "response")
    if config is None:
        return
    ctx.log.debug("Match response: {}".format(flow.request.path))
    replace = config.get("replace")
    if replace:
        response = replace.get("response", replace)
        if response:
            flow.response = make_response(response, flow.response.status_code, flow.response.content, flow.response.headers)
            ctx.log.info("Replace response {}: {}".format(flow.request.path, flow.response))
    modify = config.get("modify", [])
    if isinstance(modify, dict) or isinstance(modify, str):
        modify = [ modify ]
    global_modify = mock_config.get("response", {}).get("*", {}).get("modify")
    if global_modify:
        if isinstance(global_modify, dict) or isinstance(global_modify, str):
            global_modify = [ global_modify ]
        modify = global_modify + modify
    for modification in modify:
        if isinstance(modification, dict):
            content = {}
            try:
                content = json.loads(flow.response.text)
            except ValueError as error:
                ctx.log.info("Invalid JSON: {}: {}".format(error, flow.response.text))
            delete = modification.get("delete")
            replace = modification.get("replace")
            merge = modification.get("merge")
            if delete:
                content = delete_content(delete, content)
            if replace:
                if isinstance(replace, str):
                    try:
                        with open(replace) as replace_file:
                            replace = json.load(merge_file)
                    except FileNotFoundError:
                        pass
                if isinstance(replace, dict):
                    content.update(replace)
                elif isinstance(content, list) and isinstance(replace, list):
                    content = replace
                else:
                    if isinstance(replace, str):
                        replace = replace[1:].split(replace[0])
                    sub_re, replacement = re.compile(replace[0], re.X), replace[1]
                    flow.response.text = sub_re.sub(replacement, flow.response.text)
                    try:
                        content = json.loads(flow.response.text)
                    except ValueError as error:
                        ctx.log.error("Invalid JSON: {}: after replace: {}".format(error, replace))
            if merge:
                if isinstance(merge, str):
                    with open(merge) as merge_file:
                        merge = json.load(merge_file)
                content = merge_content(merge, content)
            flow.response.content = json.dumps(content or {}).encode("utf-8")
        else:
            if isinstance(modification, str):
                modification = modification[1:].split(modification[0])
            sub_re, replacement = re.compile(modification[0], re.X), modification[1]
            flow.response.text = sub_re.sub(replacement, flow.response.text)
    if modify:
        ctx.log.info("Modify response {}: {}".format(flow.request.path, modify))

# The global configuration.
mock_config = {}

# Regex paths for requests.
re_request = OrderedDict()

# Regex paths for responses.
re_response = OrderedDict()

# Hit counters (for `count` and `once`).
hit_count = {}

# Round-robin cycle indices (for `cycle`).
cycle_index = {}