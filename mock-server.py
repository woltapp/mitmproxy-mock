import json
import random
import re
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
    ctx.log.info("Matching {} against {}".format(host, allow))
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
    in which case returns True if `value` matches any element of `allow`.
    """
    if type(value) is type(allow):
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
    required_query = config.get("query", mock_config.get("query"))
    if required_query:
        query = request.query
        for key in required_query:
            value = required_query[key]
            if not ((key in query) and matches_value_or_list(query[key], value)):
                return False
    return True

def response_matches_config(response: http.HTTPResponse, config: dict) -> bool:
    """
    Returns whether `response` is matched by `config`. This checks the following:

    - `status` (the HTTP status code)
    - `error` (boolean true/false
    - `content`
    """

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
        ctx.log.info("Count {} for {}: {}".format(count, path, result))
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
                ctx.log.info("Reading file: {}".format(content))
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
            return content.encode("utf-8"), content_type
    else:
        return json.dumps(content).encode("utf-8"), content_type

def make_response(response, status = 200, content = {}, headers = {}) -> http.HTTPResponse:
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
    charset = response.get("charset", mock_config.get("charset"))
    if charset and not "charset" in content_type:
        content_type = "{}; charset={}".format(content_type, charset)
    headers = {**headers, **{ "Content-Type": content_type }}
    headers.update(response.get("headers", {}))
    status = response.get("status", 200)
    ctx.log.debug("Response {}: headers={} content={}".format(status, headers, content))
    return http.HTTPResponse.wrap(
        net.http.Response.make(status, content, headers),
    )

# Called when the script is loaded, registers command-line options.
def load(script) -> None:
    script.add_option("mock", str, "mock.json", "Mock configuration JSON file")

# Called to configure the script
def configure(updated) -> None:
    if "mock" in updated:
        mock_filename = ctx.options.mock
        with open(mock_filename) as mock_config_file:
            try:
                new_config = json.load(mock_config_file)
                mock_config.clear()
                mock_config.update(new_config)
                ctx.log.info("Mock configuration: {}".format(mock_filename))
            except ValueError as error:
                ctx.log.error("Error: {}: {}".format(mock_filename, error))
        if not mock_config:
            ctx.log.error("No configuration: use --set config.json")

# Called for every incoming request, before passing anything to the remote
# server. Handling requests allows mocking data for endpoints not present
# on remote, or modifying the outgoing request.
def request(flow: http.HTTPFlow) -> None:
    ctx.log.debug("Request {}: {}".format(flow.request.path, flow.request))
    path = flow.request.path.split("?")[0]
    handlers = mock_config.get("request", {})
    path_handler = handlers.get(flow.request.path, handlers.get(path))
    if path_handler is None:
        return
    config = handlers.get("*", {})
    if isinstance(path_handler, list):
        matched = None
        for handler in path_handler:
            handler_config = {**config, **handler}
            if request_matches_config(flow.request, handler_config):
                matched = handler_config
                break
        if matched is None:
            return
        config = matched
    else:
        config = {**config, **path_handler}
        if not request_matches_config(flow.request, config):
            return
    config.update(count_based_config(path, config))
    random_configs = config.get("random")
    if random_configs:
        config.update(random.choice(random_configs))
    ctx.log.info("Match {}: {}".format(flow.request.path, config))
    modify = config.get("modify")
    if modify:
        ctx.log.info("Modify: {} -> {}".format(flow.request, modify))
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
        flow.response = make_response(response)
        ctx.log.info("Mock {}: {}".format(flow.request, flow.response))

# Called before returning a response from the remote server. This can be
# used to rewrite responses based on their original contents. For example,
# the same endpoint may return multiple types of responses and we may wish
# to mock only some of them, or to mock them in different ways.
def response(flow: http.HTTPFlow) -> None:
    path = flow.request.path.split("?")[0]
    handlers = mock_config.get("response", {})
    config = {**handlers.get("*", {}), **handlers.get(path, {})}
    ctx.log.info("Response {}: {}".format(path, flow.request))
    if not request_matches_config(flow.request, config):
        return # the request did not match
    if not response_matches_config(flow.response, config):
        return # the response did not match
    config.update(count_based_config(":" + path, config))
    ctx.log.info("Match {}: {}".format(path, config))

mock_config = {}
hit_count = {}