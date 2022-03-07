# Moxy – A MITMProxy Mock Script

Moxy is a script to easily configure [mitmproxy](https://mitmproxy.org)
as a tool for app development and testing, in particular to insert mock
responses, and/or modify existing responses and/or requests
based on various criteria.

The MITM stands for man-in-the-middle, i.e., the tool goes in between the
client and server, and can capture, replace, and/or modify requests and
responses between them. The configuration is JSON, i.e., no coding is required
to use this tool. Among other things, it can do:

* mocking responses for endpoints not yet implemented on the backend
* reproducing specific flows, including error responses; one-shot,
multi-stage, state machine, cyclic, and randomly selected actions are supported
* matching specific responses from the real backend and modifying their
contents (e.g., insert new data into an existing response)
* logging occurrences of specific requests or responses
* introducing random errors to simulate unreliable conditions

## Installation

Install mitmproxy through `pip` or Homebrew:

``` sh
pip3 install mitmproxy
```

or

``` sh
brew install mitmproxy
````

Run it once to generate the certificate:

``` sh
mitmproxy
```

Press <kbd>Q</kbd> to quit.

There should now be a certificate in `~/.mitmproxy`. Add it as a trusted
root certificate on your development device. See the
[documentation for adding certificates on different platforms](https://docs.mitmproxy.org/stable/concepts-certificates/).

For iOS simulators:

``` sh
git clone https://github.com/ADVTOOLS/ADVTrustStore.git
python ADVTrustStore/iosCertTrustManager.py -a ~/.mitmproxy/mitmproxy-ca-cert.pem
```

On many devices you can also go to [mitm.it](http://mitm.it/)
while connected through `mitmproxy` and install the certificate from there.

## Running

You can run `mitmproxy` as the interactive console, or `mitmdump` that
just executes the script and logs things to the terminal. The script is
specified using the command-line argument `-s moxy.py`, and the configuration
file for the script is set with `--set mock=config.json`.

The proxy can operate in several different modes, which are detailed below. As
a short guide:

* if you can configure the server URL in the app, **reverse proxy mode** is
  by far the simplest to set up and doesn't interfere with other traffic
* if you are running the app on an actual device (e.g., a phone), or on
  a virtual device that supports proxy configuration on a system level,
  use **regular proxy mode**
* otherwise you may need to set up **transparent proxy mode**

### Reverse Proxy Mode

Reverse proxy mode means that the client connects to the address of the
machine running the proxy, which then forwards the requests to a specified
server. This means that the client must be modified to use the proxy
address/URL, but otherwise this is easy to set up without affecting other
network traffic on the device.

Reverse proxy mode can be run as follows:

``` sh
mitmdump -s moxy.py --set mock=config.json -m reverse:https://api.server.com
```

or

``` sh
./moxyserver config.json https://api.server.com
```

Here `config.json` is the name of your configuration file (see below), and
`https://api.server.com` is the real backend's address. The client is then
configured to use `https://localhost:8080/` (or your computer's IP address
if not running on the same device) as the server.

### Regular and Transparent Proxy Modes

Regular and transparent proxy modes allow passing all traffic through the
proxy without changing the original URLs, i.e., the client app does not need
to be reconfigured (and indeed, you don't even need its source code).

The default, regular proxy, mode requires proxy support and configuration in
the app or OS, and typically this means that all HTTP requests will go through
the proxy, not just the ones we are interested in.

``` sh
mitmproxy -s moxy.py --set mock=config.json
```

or

``` sh
./moxy config.json
```

As with reverse proxy mode, `config.json` is your configuration file. There
is no server address specified, since it is sent by the client when operating
through a proxy.

The transparent proxy mode requires setting up the device or a router to
redirect its HTTP/HTTPS traffic through the proxy. The benefit of this is that
it doesn't need any specific proxy configuration (or even support) on the
client. Also, in contrast to regular proxy mode, the redirection can be done
selectively (e.g., by IP address), using packet filter / firewall rules, so
that not all traffic is redirected. However,
[setting it up](https://docs.mitmproxy.org/stable/howto-transparent/)
is fairly complicated.

The transparent proxy mode is run as follows (but other configuration is
required for traffic to be redirected into the proxy):

``` sh
mitmproxy -s moxy.py --set mock=config.json -m transparent --showhost
```

Some clients may require a specific certificate instead of any trusted
certificate for that domain – this is known as certificate pinning – which
you must somehow overcome. This tool is meant for development, so it is
assumed here that as the developer you are either able to disable
certificate pinning in your own app, or whitelist the certificate used by
mitmproxy.

## Configuration

The configuration of this tool is done in JSON, which contains handlers
for requests and/or responses by path/endpoint. The JSON file is provided as
an argument when starting the proxy. It is automatically reloaded when its
modification time changes, i.e., you can edit it while the proxy is running
without interrupting operations.

The configuration file is a JSON file containing a single dictionary (map)
object. The two main top-level keys are `request` and `response`, which
contain the path-specific handlers for requests and responses,
respectively, e.g.:

``` json
{
    "request":{
        "/file":{
            "respond":{
                "content": "./config/example.json"
            }
        },
        "*":{
            "host": "api.server.com"
        }
    },
    "response":{
        "/nonexistent/path/foo":{
            "status": 404,
            "replace":{
                "status": 200,
                "content": "./config/example.json"
            }
        },
        "*":{
            "host": [
                ".server.com",
                "foo.otherserver.com"
            ]
        }
    }
}
```

The included [example configuration](config/example.json) contains many more
examples of things that are possible with this tool.

### Path Handlers

Both `request` and `response` dictionaries have paths as keys. The path
_keys_ can be any mix of the following types:

* exact string match without query or fragments, e.g. `/`, `/foo/bar`
* a regular expression match denoted by a tilde prefix `~` (which not part
  of the expression itself), evaluated in order and only in case there is no
  exact match, and _including_ query and fragments, e.g., `~html$`, `~^/v1/`
* the all-cases mix-in `*`, which is applied as the base configuration
  for that section (i.e., its contents are added to the any other handler
  unless explicitly overridden therein)

The path handler _values_ are either:

* a dictionary containing matching and action clauses, or
* an array of such dictionaries

In case the path handler is an array, its elements are evaluated in order
until the first match for the request or response is found. In particular,
note that even if there are multiple matching handlers with different
actions, only the first match is used.

#### Path Handler Definition

A path handler dictionary may contain a mix of keys for further matching
(e.g., `host` matches that path only a specific set of hosts) and for
actions to take (e.g., `pass` will pass the request without further
action).

#### Wildcard Path Handlers

The typical use of the `*` path handler is to globally specify the host
or hosts to be matched, such as in transparent proxy mode where the same
paths might exist on multiple servers. The `*` is not considered a match
by itself, i.e., if it is the only match, the path is not handled at all.
For example, the following would return the status 418 only for the path
`/foo` (which would get it from the `*` since it is not overridden by
`respond` in `/foo`):

``` json
"request":{
  "/foo":{ },
  "*":{ "respond":{ "status": 418 } }
}
```

However, the match-all regular expression `~` can be used to force all
otherwise unmatched requests or responses to be processed. The following
would return the status 418 for all paths _except_ `/foo` which, as an
exact match, prevents regular expression paths (including `~`) from being
evaluated for that path:

``` json
"request":{
  "/foo":{ },
  "~":{ "respond":{ "status": 418 } }
}
```

It is possible to define both `*` and `~`, in which case the contents of
`*` apply to `~` unless overridden therein.

### Request vs Response

A request handler is triggered when the client makes a request. This occurs
_before_ anything is sent to the server, so a request handler is good for:

* adding new endpoints that don't exist on backend
* replacing existing endpoints without triggering backend logic
* simulating error responses
* reproducing specific flows regardless of backend state
* redirecting requests

A response handler is triggered when the remote server responds to a
request. This occurs _after_ the request has already been processed by the
backend, so a request handler is good for:

* matching based on specific data returned by backend
* matching based on HTTP status code (e.g., intercepting errors)
* merging additional data into the backend response
* replacing or deleting specific parts of backend data

In short, if you can determine the desired action from the client request
alone _and_ don't need the real backend to process the request, use a
request handler. If you need the backend response (status code and/or data),
use a response handler. If you need to modify the outgoing request but also
need the backend response, use both.

### Matching

For both request and response handlers, the following keys are available
for matching:

* `scheme` – the URL scheme (`http` or `https`)
* `host` – the server hostname (e.g., `api.server.com`)
* `method` – the HTTP method (e.g., `GET`)
* `path` – the path string, including query and fragments (e.g., `/search?q=foo`)
* `query` – the query parsed into a dictionary (e.g., `{ "q": "foo" }`)
* `request` – the request body content
* `headers` – the HTTP headers as a dictionary (e.g., `{ "User-Agent": "…" }`)
* `require` – required values for global variables, which can be set by the
  the `set` action (e.g., `{ "myflag": "1" }`)

For response handlers, the following additional keys are available:

* `status` – the HTTP status code, e.g., 200
* `content` – the content sent by the server
* `error` – true/false according to the status code (400+ is an error)

Note that `headers` dictionary for response handlers is actually a combination
of the request and response headers, with response headers taking priority in
case of overlap. So the following would be valid for a
response handler `headers` condition, with `Content-Type` referring to the
response type:

``` json
"headers":{
  "User-Agent": "~Apple",
  "Content-Type": "~^text/html"
}
```

Matching may be done either as single value or an array of such values. In
case of an array, it suffices that any element matches, for example the
following matches any of the three hostnames:

``` json
"*":{
    "host":[
        "api.server.com",
        ".beta.server.com",
        "api.staging.com"
    ]
}
```

### Object Matching

The matching of objects, i.e., `content` and `query`, is done as a subset
match. For example, the following matches any JSON object returned by the
server where the key `foo` has the value `bar` and the array `arr` contains
at least one element that has the key `id` with value `1`, regardless of
any other keys and elements in any object:

``` json
"content":{
    "foo": "bar",
    "arr":[ { "id": 1 } ]
}
```

To require the presence of a key regardless of its value, the regular
expression string `~` may be used, e.g., `{ "foo": "~" }` will match if
the key `foo` is present, no matter the type or contents of its value.

Note that _keys_ must be exact matches, and do not support regular
expression strings.

#### Hostname Matching

For `host` in particular, beginning the hostname with a dot `.` causes it
to match any hostname that ends with the part following the dot, e.g.,
`.server.com` matches all of `api.server.com`, `foo.bar.server.com`, and
`server.com`. Likewise ending the hostname with a dot matches any hostname
that has a matching prefix, e.g., `api.` matches both `api.server.com` and
`api.foo.com` (but not `dev.api.server.com`).

#### Regular Expressions

Most strings in the configuration can also be used as regular expression by
beginning them with a tilde `~`, e.g., `~^foo(|ba[rz])$` matches `foo`,
`foobar` and `foobaz`.

Matching is allowed anywhere in the string, so use the special characters
`^` and `$` to denote the beginning and end, respectively.

An empty regular expression, expressed by only the plain tilde `~`, matches
everything. In `content` matching this is useful to match a dictionary key
for existence regardless of its value.

##### Paths as Regular Expression

Paths as keys to `request` and `response` can also be regular expressions.
If there is no exact match for a given path, then the regular expression
paths of that section (`request` or `response`) will be evaluated in the
order they appear in the JSON file. Beware that not all tools will preserve
the order of JSON dictionary keys, so the JSON file should be edited
manually if the order of evaluation matters.

Another way to force a deterministic order that survives JSON
transformations is to specify an array of cases for the match-all `~`
path, and test the path again inside each element:

``` json
"~":[
    {
        "path": "~^/v1/pages/"
    },
    {
        "path": "~^/v1/"
    }
]
```

### Stateful Handlers

A number of "stateful" path handlers are available:

* `once` – the contents are evaluated only once for that path
* `count` – a dictionary of handlers with specific counts as strings,
  `even`, `odd`, `~`, and/or `*` as keys, whereby all matching handlers are
  merged together such that the more specific ones take precedence
  (the id for each count is normally the path, but the `count`
  dictionary may contain an `id` key to override this)
* `cycle` – an array of handlers that are cycled through in sequence,
  wrapping around (the id for each cycle is normally the path, but
  `cycle-id` can be specified alongside `cycle` in case there are
  multiple alternate cycles for the same path, or the same cycle
  is to be used with multiple paths)
* `random` – an array of handlers from which one is chosen at random
  each time it is evaluated
* `state` – a dictionary containing the key `variable` with the name of the
  variable (settable with the `set` action) as value, and handlers for
  different cases with the value of that variable as key

These handlers allow simulating flows of responses on the same endpoint,
e.g., a sequence of events, a one-time or randomly occurring error, etc.
Note that stateful handlers can not add any further matching, because they are
evaluated only after a match has already been found.

For example, the following request handler passes any request through to
the remote server three out of four times at random, but produces a specific
error code with a one in four probability:

``` json
"~":{
  "random":[
    { "pass": true }, { "pass": true }, { "pass": true },
    {
      "respond": {
        "status": 500,
        "content": "<h1>500 - Random Error</h1>"
      }
    }
  ]
}
```

These handlers can be nested arbitrarily. Note that if you are have multiple
alternate `count` or multiple `cycle` handlers for the same path, they will
default to sharing the count or index, since it defaults to being identified
by the path. Meanwhile, for regular expression path handlers use the _actual_
path as the default identifier, and will _not_ by default share the count/index
across different paths matching the same regular expression. You can specify
`id` inside the `count` dictionary, or `cycle-id` alongside `cycle`, to
override this.

#### Variables

There is a global dictionary of variables that may be set with the `set`
action, tested for with the `require` condition, and handled case-by-case
with `state`. All variables default to the empty string.

For example, a pair of `/get` and `/set` request handlers:

``` json
"/set":[
  {
    "query": { "flag": "1" },
    "respond": "<body><h1>Flag set</h1></body>",
    "set":{ "myflag": "1" }
  },
  {
    "query": { "flag": "0" },
    "respond": "<body><h1>Flag cleared</h1></body>",
    "set":{ "myflag": "0" }
  },
  {
    "query": { "flag": "toggle" },
    "require": { "myflag": "1" },
    "set":{ "myflag": "0" },
    "respond": "<body><h1>Flag cleared (toggle)</h1></body>"
  },
  {
    "query": { "flag": "toggle" },
    "set":{ "myflag": "1" },
    "respond": "<body><h1>Flag set (toggle)</h1></body>"
  },
  {
    "respond":{
      "status": 400,
      "content": "<body><h1>400</h1><p>Usage: <code>/set?flag=(0|1|toggle)</code></p></body>"
    }
  }
],
"/get":{
  "state":{
    "variable": "myflag",
    "1":{ "respond": "<body><h1>The flag is set</h1></body>" },
    "0":{ "respond": "<body><h1>The flag is cleared</h1></body>" },
    "":{ "respond": "<body><h1>The flag is undefined</h1></body>" }
  }
}
```

### Actions

The following actions are available in both response and request handlers:

* `set` – a dictionary, sets the variables given as keys to the given values,
  which can be tested for in the `require` matcher and `state` handler (note
  that `set` takes effect immediately after matching, even if `pass` causes
  other handlers to be skipped)
* `pass` – skips any further actions and passes the request or response
  through
* `log` – logs the contents of the request or response
* `terminate` – terminates the entire moxy/mitmproxy process when matched
  (e.g., for ending automated tests gracefully)

The above actions are handled in the order listed here (i.e., `pass` will
cause `log` and/or `terminate` to not take effect).

The following actions are available only in request handlers:

* `respond` – respond with the specified response (`status`, `content`, `type`,
  `headers`) instead of requesting it from the remote server
* `modify` – a modifier dictionary containing replacement keys for the request
  (the same values are allowed as in request matching)

The following actions are available only in response handlers:

* `replace` – replaces the response with the contents of the replace
  dictionary (similar to the `respond` dictionary of request handlers)
* `modify` – a modifier dictionary or an array of modifiers, applied in order
  (see below)

#### Mocking Responses

Request handlers can be used to mock responses without ever going through
the remote server. This allows simulating events and endpoints that do
do not exist or would be hard to reproduce on backend.

The `respond` key on a request handler causes a response to be sent, and
likewise the `replace` key on a response handler causes the original
response to be replaced with the specified one.

The keys for constructing a response are:

* `status` – the HTTP status code (defaults to 200 for request handlers
  and to the original code for response handlers)
* `content` – the content either as raw string, JSON object, or a string
  containing a local filename
* `type` – a shortcut for the `Content-Type` header (in request handlers
  often inferred automatically, e.g., `application/json` for JSON objects
  and files with the extension `.json`)
* `headers` – a dictionary of headers

If the `respond` or `replace` is in itself a string, that string is
interpreted as though it were the value of `content` inside a dictionary.

Examples of request handlers:

``` json
{
  "request":{
    "/string":{
      "respond":{
        "type": "text/html",
        "content": "<body><h1>HTML</h1></body>"
      }
    },
    "/file":{
      "respond":{
        "content": "./config/example.json"
      }
    },
    "/object":{
      "respond":{
        "content": {
          "embedded":{
            "json": [ "object" ]
          }
        }
      }
    }
  }
}
```

#### Modifying Content

It is possible to modify the request or response content by using a `replace`
or `modify` key in a response handler, or a `content` key inside a request
`modify` dictionary. The format of the latter is the same as the `modify`
for `response`.

To **replace** a response entirely, specify the new response inside
the `replace` dictionary.

To **modify** content, the value of `modify` (response) or `modify["content"]`
(request) is either a dictionary or an array of such dictionaries,
processed in order, with the following keys each:

* `replace` – perform selective replacement of content (in contrast to
  the aforementioned top level `replace` which replaces the entire
  response)
* `delete` – selectively delete content
* `merge` – merge (add/insert/override) content

##### Replace

The `modify` `replace` can be of the following formats:

* a string containing a local file name, in which case the file is
  read as JSON, then processed as below
* a dictionary: the response content is interpreted as a dictionary,
  and merged with `replace` non-recursively such that any colliding
  keys are taken from `replace`
* a string of the format `/re/sub` where `/` is an arbitrary separator
  character, `re` is a regular expression, and `sub` is the substitute
  used for every occurrence of `re` in the content _string_ (note that
  this can break JSON format)
* an array with two strings as elements: the first string is treated
  as a regular expression, and all occurrences of it in the content
  are substituted by the second string

The regular expression substitution strings of the last two cases may contain
group references, e.g., `|ba[rz]|Ba\\1` would uppercase the `b` in both
`bar` and `baz`, but not in `bat`.

Note that response handlers can have a top-level `replace`, i.e., not nested
inside `modify`, which is different in that it replaces the response entirely.

##### Delete

The `modify` `delete` can be any nested JSON object. Any matching
key, element, or value is deleted from content. The match need not be
exact, i.e., as for content matching, it suffices to be a subset of
`content`. The empty dictionary `{}` can be used to match any value,
e.g., the following deletes the key `foo` regardless of its value:

``` json
"delete":{
    "foo": {}
}
```

##### Merge

The `modify` `merge` can be any nested JSON object. It is merged
recursively with the content such that any new keys and values are
inserted and any matching existing keys are replaced on the
innermost level of nesting.

If any dictionary or array member value nested inside `merge` is a string
beginning with a dot `.` and ending with `.json`, the corresponding file is
loaded and substituted for the string before merging.

For example:

``` json
"modify":[
  "|old regex|REPLACEMENT STRING",
  {
    "merge":{
      "some_new_key": "inserted item",
      "some_existing_key": "replaced item",
      "array":[
        { "id": 42, "note": "added element" }
      ]
    }
  },
  {
    "delete":{
      "some_existing_key": {},
      "array":[ { "id": 1 } ],
      "array_to_delete_entirely": {}
    }
  },
  {
    "replace":{
      "other_array": [
        { "note": "only element in array after replacing" }
      ]
    }
  }
]
  ```

###### Merge Where

As a special case, if an existing value in the content is an array,
it is possible to target specific elements of the array by using a
dictionary with a `where` object in place of the array in `merge`.

The `where` dictionary may contain the following keys:

* `where` – an object that is matched against elements of the array;
  any modifications are done only on matching elements
* `replace` or `content` – a replacement object that overwrites any elements
  in the array matching the `where` object
* `merge` – an object that is merged (only) with elements in the array
  matching the `where` object
* `insert` – a string, either `before` or `after`, which causes the resulting
  element to be inserted before or after the matched element, rather than
  replacing it (typically paired with `content` for the new object)
* `move` – a string, either `head` or `tail`, which causes any elements
  matching `where` to be moved to the head (beginning) or the tail (end) of
  the array, respectively
* `forall` – a boolean, if `true` (the default), the modifications are
  applied to all elements matching `where`, otherwise only to the first match
* `negated` –  a boolean, if `true` causes the `where` condition to be negated,
  i.e., all of the above applies to elements _not_ matching `where`
* `delete` – a boolean, if `true` causes any elements matching `where` to be
  deleted from the array (this is largely superfluous due to the `delete`
  operation, but when combined with `forall` and/or `negated`, it adds new
  possibilities)

For example:

``` json
"modify":[
  {
    "merge":{
      "array":{
        "where": { "id": 1 },
        "merge": { "note": "modified element" }
      }
    }
  },
  {
    "merge":{
      "array":{
        "where": { "id": 2 },
        "replace": { "id": 2, "note": "replaced element" },
        "move": "tail"
      }
    }
  },
  {
    "merge":{
      "array":{
        "where":{ "id": 42 },
        "move": "head"
      }
    }
  }
]
```

###### Merge Replace With

Sometimes `merge` is desired to combine values on the top level(s) of a
dictionary, but a nested dictionary or array should still be replaced entirely.
While this can be done as a multi-step modification (first delete the old
values and then merge with the new), there is a shorthand available:
if a value anywhere inside a `merge` is a dictionary containing only the key
`replace_with`, the value corresponding to the dictionary position is
replaced entirely with the value of that key.

For example:

``` json
"modify":{
  "merge":{
    "top_level": {
      "nested_array": {
        "replace_with": {
          "new_value": [ "new_array" ]
        }
      }
    }
  }
}
```

###### Merge Replace With

Similarly to `replace_with`, a `merge` dictionary may contain only the key
`replace_in`, in which case the value of that key is treated as a regular
expression substitution similarly to `replace` and applied to the corresponding
value in the content.

For example:

``` json
"modify":{
  "merge":{
    "top_level": {
      "string_key": {
        "replace_in": [ "$", " text added to the end" ]
      }
    }
  }
}
```

Similarly to `replace`, the `replace_in` replacement treats the value as an
string, so when applied to JSON dictionary or array values, it is possible to
produce invalid JSON as the output.

## Authors

* [Kimmo Kulovesi](https://github.com/arkku) – original design and implementation
* [Scott Lyttle](https://github.com/scottlyttle) – the name "Moxy"
* [Max Kovalev](https://github.com/0neel) – fix for obsolete `wrap` function call

## License and Copyright

[MIT license](./LICENSE), © [Wolt Enterprises](https://github.com/woltapp)
