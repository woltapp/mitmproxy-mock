# MITMProxy Mock

This repository contains scripts for using [mitmproxy](https://mitmproxy.org)
to insert mock responses for http requests for the purposes of app development.

## Installation

Install `mitmproxy` through `pip` or Homebrew:

```
pip3 install mitmproxy
```

or

```
brew install mitmproxy
````

Run it once to generate the certificate:

```
mitmproxy
```

Press <kbd>Q</kbd> to quit.

There should now be a certificate in `~/.mitmproxy`. Add it as a trusted root
certificate on your development device. See the [documentation for adding
certificates on different platforms](https://docs.mitmproxy.org/stable/concepts-certificates/).

For iOS simulators:

```
git clone https://github.com/ADVTOOLS/ADVTrustStore.git
python ADVTrustStore/iosCertTrustManager.py -a ~/.mitmproxy/mitmproxy-ca-cert.pem
```

On many devices you can also go to [mitm.it](http://mitm.it/) while connected
through `mitmproxy` and install the certificate from there.