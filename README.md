# MITMProxy Mock

This repository contains scripts for using [mitmproxy](https://mitmproxy.org)
to insert mock responses for http requests for the purposes of app development.

MITM stands for man-in-the-middle, i.e., the tool goes in between the client
and server, and can caputer, replace, and/or modify requests and responses
between them. This script is intended specifically to aid development of
applications, e.g.:

* mocking responses for endpoints not yet implemented on the backend
* reproducing specific flows, including error responses; one-shot,
multi-stage, cyclic, and randomly selected actions are supported
* matching specific responses from the real backend and modifying their
contents (e.g., insert new data into an existing response)

## Installation

Install Mitmproxy through `pip` or Homebrew:

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

### Reverse Proxy Mode

Reverse proxy mode means that the client connects to the address of the
machine running the proxy, which then forwards the requests to a specified
server. This means that the client must be modified to use the proxy
address/URL, but otherwise this is easy to set up without affecting other
network traffic on the device.

Reverse proxy mode can be run as follows:

``` sh
mitmdump -s mock.py --set mock=config.json -m reverse:https://api.server.com/
```

Here `config.json` is the name of your configuration file (see below), and
`https://api.server.com` is the real backend's address. The client is then
configured to use `https://localhost:8080/` (or your computer's IP address
if not running on the same device) as the server.

### Transparent Proxy Mode

Transparent proxy mode allows passing all traffic through the proxy without
changing the original URLs. This means that URLs need not be changed
(allowing this mode to be used even when you don't have access to the
client source code or configuration). However, using the proxy means you
need application or OS level support to proxy the traffic.

Some clients may also require a specific certificate instead of any trusted
certificate for that domain – this is known as certificate pinning – which
you must somehow overcome. This tool is meant for development, so it is
assumed here that as the developer you are either able to disable
certificate pinning of your own app, or whitelist the certificate used by
Mitmproxy.

The transparent proxy mode is run as follows:

``` sh
mitmdump -s mock-server.py --set mock=config.json -m transparent
```

As with reverse proxy mode, `config.json` is your configuration file. There
is server address specified, since it is sent by the client when operating
through a proxy.

## Configuration

The main benefit of using this script instead of writing your own is the
simplicity of configuration. However, the configuration itself supports
many things, and is not particularly simple to understand or describe, so
I recommend going by example.

The configuration file is a JSON file containing a single dictionary (map)
object. The two main top-level keys are `request` and `response`, which
contain the path-specific handlers for requests and responses,
respectively, e.g.:

``` json
{
    "request":{
        "/file":{
            "response":{
                "content": "./example.json"
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
                "content": "./example.json",
            }
        },
        "*":{
            "host": [
                ".server.com"
                "foo.otherserver.com"
            ]
        }
    }
}
```

### Path Handlers

Both `request` and `response` dictionaries have paths as keys. The path keys
can be any mix of the following types:

* exact string match without query or fragments, e.g. `/`, `/foo/bar`
* a regular expression match denoted by a tilde prefix `~` (which not part
  of the expression itself), evaluated in order and only in case there is no
  exact match, and _including_ query and fragmets, e.g., `~html$`, `~^/v1/`
* the all-cases mixin `*`, which is applied as the base configuration
  for that section (i.e., its contents are added to the any other handler
  unless explicitly overridden therein)

The typical use of `*` is to globally specify the host or hosts to be
matched, in case of transparent proxy mode where the same paths might
exist on other servers. The `*` is not considered a match by itself, i.e.,
if it is the only match, the path is not handled at all. The match-all
regular expression `~` can be used to force all requests or responses to
be processed. For example, the following would return the status 418 only
for the path `/foo` (which would get it from the `*` since it is not
overridden by `response` in `/foo`):

``` json
"request":{
    "/foo":{ },
    "*":{ "response": { "status": 418 } }
}
```

Whereas the following would return the status 418 for all paths _except_
`/foo` which, as an exact match, prevents regular expression paths
(including `~`) from being evaluated for that path:

``` json
"request":{
    "/foo":{ },
    "~":{ "response":{ "status": 418 } }
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

* `scheme` (e.g., `https`)
* `host` (e.g., `api.server.com`)
* `path` (e.g., `/v1/pages/front`, done on full path including args and fragments)
* `query` (e.g., `{ "q": "query" }`)

For response handlers, the following additional keys are available:

* `status` (the HTTP status code, e.g., 200)
* `content` (the content sent by the server)

Matching may be done either as single value or an array of such values. In
case of an array, it suffices that any element matches, for example the
following matches any of the three hostnames:

``` json
"*":{
    "host":[ 
        "api.server.com",
        "beta.server.com",
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
paths of that section (`request` or `response`) will be evaluated in the order they appear in the JSON file. Beware that not all tools will preserve
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