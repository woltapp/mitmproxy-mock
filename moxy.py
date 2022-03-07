#
# moxy.py: A mitmproxy script for mocking/modifying server responses.
#
# The mock configuration is loaded from a JSON file, e.g.:
#
#   mitmdump -s moxy.py --set mock=example.json -m reverse:https://api.foo.com/
#
# See config/example.json and README.md for examples and documentation.
#
# Authors:
# * Kimmo Kulovesi (design and initial version), https://github.com/arkku
#
# Copyright © 2020–2021 Wolt Enterprises
#

import json
import os
import random
import re
from collections import OrderedDict
from typing import Optional, Tuple, Union
from mitmproxy import http
from mitmproxy import ctx
from mitmproxy import net

def host_matches(host: str, allow) -> bool:
    """
    Returns whether `host` matches `allow`.

    `allow` may be a string pattern, or a list of such patterns, in which
    case returns True if `host` matches any pattern in `allow`.

    - If the pattern begins with a dot, `host` must end with the suffix
      following the dot
    - If the patterns ends with a dot, `host` must start with the pattern
    - If the patterns begins with a tilde, the rest of the pattern is treated as
      a regular expression that must be found in `host`
    - Otherwise `host` must equal the pattern
    """
    if isinstance(allow, str):
        if allow.startswith("."):
            return host.endswith(allow[1:])
        elif allow.endswith("."):
            return host.startswith(allow)
        elif allow.startswith("~"):
            return bool(compiled_re_for(allow[1:]).search(host))
        else:
            return host == allow
    elif isinstance(allow, dict):
        return bool(allow.get(host, False))
    elif allow is None:
        return True
    else:
        for allowed_host in allow:
            if host_matches(host, allowed_host):
                return True
    return False

def compiled_re_for(re_str: str):
    """
    Returns a compiled regular expression object for the string `re_str`.
    The compiled regular expressions are cached in memory.
    """
    global re_cache
    result = re_cache.get(re_str)
    if result is None:
        result = re.compile(re_str, re.X)
        re_cache[re_str] = result
    return result

def matches_value_or_list(value, allow) -> bool:
    """
    Returns whether `value` matches `allow`.

    `allow` may either be of the same type as `value`, or a list of such items,
    in which case returns True if `value` matches any element of `allow`. In
    case of strings, value may have a tilde prefix (`~`) in which case its
    suffix is treated as a regular expression.
    """
    if type(value) is type(allow):
        if isinstance(allow, str) and allow.startswith("~"):
            return (value == allow) or bool(compiled_re_for(allow[1:]).search(value))
        else:
            return value == allow
    elif isinstance(allow, dict):
        return allow.get(value, False)
    elif isinstance(allow, str):
        return allow == str(value)
    else:
        for allowed in allow:
            if matches_value_or_list(value, allowed):
                return True
    return False

def request_matches_config(request: http.Request, config: dict) -> bool:
    """
    Returns whether `request` is matched by `config`. This checks the following:

    - `host` (some patterns supported, see `host_matches`)
    - `scheme` (exact match or list)
    – `method` (exact match or list)
    - `path` (exact match or list, normally matched already before coming here)
    - `query` (keys are exact, values either exact or list)
    – `request` (the content of the request body)
    – `require` (dictionary from variable names to required values)
    """
    if not config:
        return False
    host_whitelist = config.get("host", mock_config.get("host"))
    if not host_matches(str(request.host), host_whitelist):
        return False
    required_scheme = config.get("scheme", mock_config.get("scheme"))
    if required_scheme and not matches_value_or_list(request.scheme, required_scheme):
        return False
    required_method = config.get("method")
    if required_method and not matches_value_or_list(request.method, required_method):
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
    required_content = config.get("request")
    if required_content and not content_matches(request.text, required_content):
        return False
    required_state = config.get("require")
    if required_state:
        if isinstance(required_state, dict):
            for variable, required_value in required_state.items():
                value = mock_state.get(variable, "")
                if not matches_value_or_list(value, required_value):
                    return False
        else:
            variable = config.get("variable", request.path.split("?")[0])
            if not matches_value_or_list(mock_state.get(variable, ""), required_state):
                return False
    return True

def is_subset(subset, superset) -> bool:
    """
    Returns whether `subset` is indeed a subset of `superset`. That is, all
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
                allow_re = compiled_re_for(subset[1:])
                return bool(allow_re.search(str(superset)))
            else:
                return str(superset) == subset
        else:
            return subset == superset
    except Exception as error:
        ctx.log.debug("is_subset incompatible types: {}: {} {}".format(error, subset, superset))
        return False

def content_matches(content_str: Optional[str], allow: Union[str,list,dict], content_object: Optional[Union[dict,list]] = None) -> bool:
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
    if isinstance(allow, str) or isinstance(allow, dict):
        allow = [ allow ]
    for allowed in allow:
        try:
            if isinstance(allowed, str):
                if content_str is None:
                    content_str = content_as_str(content_object) or str(content_object)
                if allowed.startswith("~"):
                    allow_re = compiled_re_for(allowed[1:])
                    if not allow_re.search(content_str):
                        return False
                elif not allowed in content_str:
                    return False
            elif isinstance(allowed, dict):
                if content_object is None:
                    content_object = content_as_object(content_str) or {}
                if not is_subset(allowed, content_object):
                    return False
            elif not content_matches(content_str, allowed, content_object):
                return False
        except Exception as error:
            ctx.log.info("Error: {}: matching {}".format(error, allowed))
            return False
    return True

def response_matches_config(response: Optional[http.Response], config: dict) -> bool:
    """
    Returns whether `response` is matched by `config`. This checks the following:

    - `status` (the HTTP status code)
    - `error` (true iff the HTTP status >= 400)
    - `content` (a string or a list of strings where _all_ must match)

    For content matching, each string can either be a regular expression denoted
    by a tilde prefix (`~`), otherwise a substring that must be found exactly.
    """
    if not response:
        return False
    required_status = config.get("status")
    if required_status and not matches_value_or_list(response.status_code, required_status):
        return False
    required_error_state = config.get("error")
    if isinstance(required_error_state, bool) and required_error_state != (response.status_code >= 400):
        return False
    required_content = config.get("content")
    if required_content and not content_matches(response.text, required_content):
        return False
    return True

def resolve_value(value):
    """
    Resolves `value` into the final, expanded value, e.g., loads file contents
    referenced from value strings.
    """
    if isinstance(value, str) and (value.startswith(".") and (value.endswith(".json") or value.endswith(".js"))):
        try:
            with open(value) as value_file:
                value = json.load(value_file)
        except:
            pass
    return value

def merge_content(merge, content):
    """
    Merges `merge` into `content` recursively for dictionaries and lists.
    """
    merge = resolve_value(merge)
    if isinstance(merge, dict):
        if len(merge) == 1 and ("replace_with" in merge):
            content = resolve_value(merge["replace_with"])
        elif len(merge) == 1 and ("replace_in" in merge):
            content = replace_in_content(merge["replace_in"], content)
        elif isinstance(content, dict):
            for key in merge:
                content[key] = merge_content(merge[key], content.get(key))
        elif isinstance(content, list) and ("where" in merge):
            where = merge["where"]
            match_condition = not bool(merge.get("negated", False))
            match_move = merge.get("move")
            match_insert = merge.get("insert")
            index, end_index = 0, len(content)
            while index < end_index:
                element = content[index]
                if bool(is_subset(where, element)) == match_condition:
                    new_element = element
                    if "replace" in merge:
                        new_element = merge_content(merge["replace"], None)
                    elif "content" in merge:
                        new_element = merge_content(merge["content"], None)
                    if "merge" in merge:
                        new_element = merge_content(merge["merge"], new_element or {})
                    elif merge.get("delete"):
                        new_element = None
                    if new_element is None:
                        del content[index]
                        end_index -= 1
                    elif match_move:
                        del content[index]
                        if match_move == "head" or match_move == "first":
                            content.insert(0, new_element)
                            index += 1
                        else:
                            content.append(new_element)
                            end_index -= 1
                    elif match_insert:
                        if match_insert == "before":
                            content.insert(index, new_element)
                        else:
                            content.insert(index + 1, new_element)
                        index += 1
                        end_index += 1
                    else:
                        content[index] = new_element
                        index += 1
                    if not merge.get("forall", True):
                        break
                else:
                    index += 1
        else:
            content = {}
            for key in merge:
                content[key] = merge_content(merge[key], None)
    elif isinstance(merge, list):
        merge = list(map(resolve_value, merge))
        if content is None:
            content = []
        elif not isinstance(content, list):
            content = [ content ]
        for element in merge:
            content.append(merge_content(element, None))
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

def content_as_str(content) -> str:
    """
    Returns `content` as a string, converting to JSON if necessary.
    """
    if isinstance(content, str):
        return content
    elif content is None:
        return ""
    try:
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        else:
            content = json.dumps(content)
    except Exception as error:
        ctx.log.info("Error converting to text: {}: {}".format(error, content))
        content = ""
    return content

def content_as_object(content):
    """
    Returns `content` as an object, converting from JSON if necessary.
    """
    if isinstance(content, str) or content is None:
        try:
            content = json.loads(content)
        except Exception as error:
            ctx.log.info("Error loading JSON: {}: {}".format(error, content))
            content = {}
    return content

def replace_in_content(replace: Union[str,list,dict], content):
    """
    Performs replacement `replace` (dict update or regex sub) in `content`.
    """
    if isinstance(replace, dict):
        content = content_as_object(content) or {}
        try:
            content.update(replace)
        except Exception:
            content = replace
    elif replace:
        if isinstance(replace, str):
            fields = replace[1:].split(replace[0])
            if len(fields) == 2:
                replace = fields
            else:
                return replace
        sub_re, replacement = compiled_re_for(replace[0]), replace[1]
        try:
            content_is_str = isinstance(content, str)
            content = sub_re.sub(replacement, content_as_str(content))
            if not content_is_str:
                content = content_as_object(content)
        except ValueError as error:
            ctx.log.error("Invalid JSON: {}: after replace: {}".format(error, replace))
    return content

def modify_content(modify: Union[str,list,dict], content):
    """
    Returns `content` modified according to all elements of `modify`.
    """
    if isinstance(modify, dict) or isinstance(modify, str):
        modify = [ modify ]
    for modification in modify:
        if isinstance(modification, dict):
            delete = modification.get("delete")
            replace = modification.get("replace")
            merge = modification.get("merge")
            if delete:
                content = delete_content(delete, content_as_object(content))
            if replace:
                if isinstance(replace, str):
                    try:
                        with open(replace, "rb") as replace_file:
                            text = replace_file.read().decode("utf-8")
                            try:
                                replace = json.loads(text)
                            except ValueError:
                                replace = text
                    except Exception:
                        pass
                if isinstance(replace, str):
                    content = replace
                else:
                    content = replace_in_content(replace, content)
            if merge:
                if isinstance(merge, str):
                    with open(merge) as merge_file:
                        merge = json.load(merge_file)
                content = merge_content(merge, content_as_object(content))
        else:
            try:
                if isinstance(modification, str):
                    modification = modification[1:].split(modification[0])
                sub_re, replacement = compiled_re_for(modification[0]), modification[1]
                content = sub_re.sub(replacement, content_as_str(content))
            except Exception as error:
                ctx.log.info("Error modifying with {}: {}".format(modification, error))
    return content

def encode_content(content: Union[str,list,dict]) -> Tuple[bytes, str]:
    """
    Return a tuple of `content` encoded into bytes, and a guess of its type.

    `content` may be any of the following:
    - A file name string, in which case the content is loaded from the file and
      type inferred from the file's extension (json, js, html, xml, txt, md).
    - A raw string, in which case it is encoded according as UTF-8, and the type
      is inferred to be HTML if it starts with a `<`, otherwise JSON.
    - A list or dictionary that can be dumped as JSON, in which case
      it is processed as per `merge_content` and converted to UTF-8 JSON.
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
    else:
        try:
            processed_content = content
            if isinstance(content, dict):
                processed_content = merge_content(content, {})
            elif isinstance(content, list):
                processed_content = merge_content(content, [])
            if processed_content:
                content = processed_content
        except:
            pass
    return content_as_str(content).encode("utf-8"), content_type + "; charset=utf-8"

def make_response(response: Union[str,dict], status, content, headers) -> http.Response:
    """
    Return a new `Response` object constructed from the configuration
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
    merge_headers = response.get("headers", {})
    if isinstance(merge_headers, dict):
        headers.update(merge_headers)
    status = response.get("status", status)
    ctx.log.debug("Response {}: headers={} content={}".format(status, headers, content))
    try:
        return http.Response.make(int(status), content, headers)
    except NameError:
        # Backwards compatibility with mitmproxy < 7.0.0
        return http.HTTPResponse.make(status, content, headers)


def extract_regex_paths(config: OrderedDict) -> OrderedDict:
    """
    Returns an `OrderedDict` of compiled regex paths from `config`.

    A regex path in `config` is a string with a tilde prefix (`~`),
    where the rest of the string is a regular expression. Those
    paths are taken in order, compiled, an added to the resulting
    ordered dictionary in the same order.
    """
    re_paths = OrderedDict()
    if config:
        for path, handler in config.items():
            if not path.startswith("~"):
                continue
            try:
                path_re = compiled_re_for(path[1:])
                re_paths[path_re] = json.loads(json.dumps(handler))
            except Exception as error:
                ctx.log.error("Error: regex path {}: {}".format(path, error))
    return re_paths

def load_config_file(mock_filename: str) -> None:
    """
    Loads the configuration file `mock_filename`, replacing the global config.
    """
    global mock_config, re_request, re_response, mock_state
    global hit_count, cycle_index, config_modified_at
    ctx.log.info("Loading mock configuration {}".format(mock_filename))
    try:
        with open(mock_filename) as mock_config_file:
            # OrderedDict hack to preserve path regex order
            config_modified_at = os.path.getmtime(mock_filename)
            ordered_config = json.load(mock_config_file, object_pairs_hook=OrderedDict)
            request_re_paths = extract_regex_paths(ordered_config.get("request"))
            response_re_paths = extract_regex_paths(ordered_config.get("response"))
            mock_config = json.loads(json.dumps(ordered_config))
            re_request, re_response = request_re_paths, response_re_paths
            hit_count.clear()
            cycle_index.clear()
            mock_state.clear()
    except Exception as error:
        ctx.log.error("Error: {}: {}".format(mock_filename, error))
    if not mock_config:
        ctx.log.error("No configuration: use --set config.json")

def reload_config_if_updated(mock_filename: Optional[str] = None) -> None:
    """
    Reloads the configuration file `mock_filename` if it has been modified.
    """
    try:
        if not mock_filename:
            mock_filename = str(ctx.options.mock)
        timestamp = os.path.getmtime(mock_filename)
        if timestamp != config_modified_at:
            load_config_file(mock_filename)
    except Exception as error:
        ctx.log.error("Error: {}: {}".format(mock_filename, error))

def count_based_config(path: str, count_config: dict) -> dict:
    """
    Returns `count_config` reduced to the merged configuration for this
    iteration based on the number of times `path` has been hit.

    The configuration dictionary is formed from these keys applied
    on top of one another in the following order:
    - `*`: applied to every count
    - `odd` or `even`: applied to alternating counts
    - `1`, `2`, `3`, …: applied to exact counts, starting from 1
    - `~`: applied in case there are no matching exact counts

    Note that the keys of `config["count"]` are strings, even for the numeric
    counts, since the data is loaded from JSON.
    """
    global hit_count
    result = {}
    if count_config:
        count_id = count_config.get("id", path)
        count = hit_count.get(count_id, 0) + 1
        hit_count[count_id] = count
        result.update(count_config.get("*", {}))
        result.update(count_config.get("even" if (count % 2) == 0 else "odd", {}))
        specific_config = count_config.get(str(count), count_config.get(count))
        if specific_config is None:
            specific_config = count_config.get("~", {})
        result.update(specific_config or {})
    return result

def state_based_config(variable: str, state_config: dict) -> dict:
    """
    Returns `state_config` reduced to the merged configuration for this value
    of `variable` in the global `mock_state`.

    - `*`: applied to every state
    - the exact value of `variable`
    - `~` applied in case the exact value is not found
    """
    global mock_state
    result = {}
    result.update(state_config.get("*", {}))
    value = mock_state.get(variable, "")
    if value in state_config:
        result.update(state_config.get(value, {}))
    else:
        result.update(state_config.get("~", {}))
    return result

def resolve_config_state(path: str, config: dict, is_copy: bool = False) -> dict:
    """
    Returns a copy of `config` after all stateful handlers have been resolved
    to the current state. The stateful handlers are:

    - `set` – dictionary from variable name to value, set globally
    - `once` – handler executed only once, shares the count with `count`
    - `count` – visit-count based handler
    - `cycle` – array of handlers, chosen in sequence with wrap-around
    - `random` – an array of handlers, a random one is chosen every time
    - `state` – a dictionary with the key `variable` for a variable name,
      and different handlers keyed by values of that variable
    """
    global cycle_index, mock_state
    if "set" in config:
        if not is_copy:
            config, is_copy = {**config}, True
        set_config = config.pop("set")
        if set_config:
            if isinstance(set_config, dict):
                for variable, value in set_config.items():
                    mock_state[variable] = value
            else:
                variable = config.get("variable", path)
                mock_state[variable] = set_config
    if "once" in config:
        if not is_copy:
            config, is_copy = {**config}, True
        once_config = config.pop("once")
        if once_config:
            config.update(count_based_config(path, { "1": once_config }))
            return resolve_config_state(path, config, is_copy)
    if "count" in config:
        if not is_copy:
            config, is_copy = {**config}, True
        count_config = config.pop("count")
        if count_config:
            config.update(count_based_config(path, count_config))
            return resolve_config_state(path, config, is_copy)
    if "cycle" in config:
        if not is_copy:
            config, is_copy = {**config}, True
        cycle = config.pop("cycle")
        if cycle:
            cycle_id = config.get("cycle-id", path)
            index = cycle_index.get(cycle_id, 0)
            cycle_index[cycle_id] = index + 1
            config.update(cycle[index % len(cycle)])
            return resolve_config_state(path, config, is_copy)
    if "random" in config:
        if not is_copy:
            config, is_copy = {**config}, True
        random_configs = config.pop("random")
        if random_configs:
            config.update(random.choice(random_configs))
            return resolve_config_state(path, config, is_copy)
    if "state" in config:
        if not is_copy:
            config, is_copy = {**config}, True
        state_config = config.pop("state")
        if state_config:
            variable = state_config.get("variable", config.get("variable", path))
            config.update(state_based_config(variable, state_config))
            return resolve_config_state(path, config, is_copy)
    return config

def resolve_config(flow: http.HTTPFlow, event: str) -> Optional[dict]:
    """
    Returns configuration for the event (`request` or `response`) from
    the flow state and the global mock configuration, or `None` if no
    configuration item matches the event.
    """
    reload_config_if_updated()
    is_request = (event == "request")
    path = flow.request.path.split("?")[0]
    handlers = mock_config.get(event, {})
    path_handler = handlers.get(flow.request.path, handlers.get(path))
    if path_handler is None:
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
    config = resolve_config_state(path, config)
    if config.get("pass", False):
        return None
    msg = config.get("log")
    if msg:
        if msg is True:
            msg = "Log"
        if is_request:
            ctx.log.info("{}: {}".format(msg, flow.request))
        else:
            ctx.log.info("{}: {} -> {}".format(msg, flow.request, flow.response))
    if config.get("terminate", False):
        ctx.log.info("Terminate {}".format(config.get("terminate")))
        ctx.master.shutdown()
    return config

def save_flow(save, flow: http.HTTPFlow, event: str) -> None:
    # TODO: Save to file(s) according to `save` definition
    pass

# Called for every incoming request, before passing anything to the remote
# server. Handling requests allows mocking data for endpoints not present
# on remote, or modifying the outgoing request.
def request(flow: http.HTTPFlow) -> None:
    ctx.log.debug("Request {}: {}".format(flow.request.path, flow.request))
    config = resolve_config(flow, "request")
    if config is None:
        return
    required_headers = config.get("headers")
    if required_headers and not content_matches(None, required_headers, dict(flow.request.headers)):
        return
    ctx.log.debug("Match request {}: {}".format(flow.request.path, config))
    save = config.get("save", mock_config.get("save"))
    if save:
        save_flow(save, flow, "request")
    modify = config.get("modify")
    if modify:
        ctx.log.debug("Modify request: {} -> {}".format(flow.request, modify))
        flow.request.scheme = modify.get("scheme", flow.request.scheme)
        flow.request.host = modify.get("host", flow.request.host)
        flow.request.path = modify.get("path", flow.request.path)
        flow.request.method = modify.get("method", flow.request.method)
        query_modifier = modify.get("query")
        if query_modifier:
            query = flow.request.query or {}
            if isinstance(query_modifier, str) or isinstance(query_modifier, list):
                flow.request.query = content_as_object(modify_content(query_modifier, dict(query)))
            else:
                flow.request.query = {**query, **query_modifier}
        flow.request.headers.update(modify.get("headers", {}))
        modifier = modify.get("content")
        if modifier is not None:
            content = flow.request.text or ""
            flow.request.text = content_as_str(modify_content(modifier, content))
    response = config.get("respond")
    if response:
        flow.response = make_response(response, 200, "", {})
        ctx.log.debug("Mock {}".format(flow.request.path))

# Called before returning a response from the remote server. This can be
# used to rewrite responses based on their original contents. For example,
# the same endpoint may return multiple types of responses and we may wish
# to mock only some of them, or to mock them in different ways.
def response(flow: http.HTTPFlow) -> None:
    ctx.log.debug("Response {}: {}".format(flow.request.path, flow.response))
    config = resolve_config(flow, "response")
    if config is None:
        return
    required_headers = config.get("headers")
    if required_headers:
        headers = {**dict(flow.request.headers), **dict(flow.response.headers)}
        if not content_matches(None, required_headers, headers):
            return
    ctx.log.debug("Match response {}: {}".format(flow.request.path, config))
    save = config.get("save", mock_config.get("save"))
    if save:
        save_flow(save, flow, "response")
    replace = config.get("replace")
    if replace:
        response = replace.get("response", replace)
        if response:
            flow.response = make_response(response, flow.response.status_code, flow.response.content, flow.response.headers)
            ctx.log.debug("Replace response {}: {}".format(flow.request.path, flow.response))
    modify = config.get("modify", [])
    if isinstance(modify, dict) or isinstance(modify, str):
        modify = [ modify ]
    global_modify = mock_config.get("response", {}).get("*", {}).get("modify")
    if global_modify:
        if isinstance(global_modify, dict) or isinstance(global_modify, str):
            global_modify = [ global_modify ]
        modify = global_modify + modify
    if modify:
        flow.response.text = content_as_str(modify_content(modify, flow.response.text))
        ctx.log.debug("Modify response {}: {}".format(flow.request.path, modify))

# Called when an error (inside mitmproxy, not from the remote server) occurs.
def error(flow: http.HTTPFlow):
    ctx.log.debug("Error {}: {}".format(flow.request.path, flow.error))

# Called when the script is loaded, registers command-line options.
def load(script) -> None:
    script.add_option("mock", str, "mock.json", "Mock configuration JSON file")

# Called to configure the script.
def configure(updated) -> None:
    if "mock" in updated:
        load_config_file(ctx.options.mock)

# The global configuration.
mock_config = {}

# The global state (can be set and matched by rules).
mock_state = {}

# Compiled regular expressions indexed by string.
re_cache = {}

# Regex paths for requests.
re_request = OrderedDict()

# Regex paths for responses.
re_response = OrderedDict()

# Hit counters (for `count` and `once`).
hit_count = {}

# Round-robin cycle indices (for `cycle`).
cycle_index = {}

# The modification time of the config file.
config_modified_at = None
