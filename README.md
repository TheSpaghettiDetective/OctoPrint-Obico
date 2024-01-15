# Obico for OctoPrint

[Obico](https://www.obico.io) is a community-built, open-source smart 3D printing platform used by makers, enthusiasts, and tinkerers around the world.


This plugin requires Obico Server to work. You can grab the server source code from the [Obico Server github repo](https://github.com/TheSpaghettiDetective/obico-server) and run a self-hosted Obico Server. If you don't want the hassle of setting up and running your own server, you can [sign up a Obico Cloud account](https://www.obico.io/accounts/signup/).

## Setup

Install via the bundled [Plugin Manager](https://github.com/foosel/OctoPrint/wiki/Plugin:-Plugin-Manager)
or manually using this URL:

    https://github.com/TheSpaghettiDetective/OctoPrint-Obico/archive/master.zip

## Configuration

Follow [Obico Setup Guide](https://www.obico.io/docs/user-guides/octoprint-plugin-setup/) to set up this plugin.


# Plugin Development

## Running the plugin locally

```bash
docker compose up -d
```

Will start a series of containers that support the plugin (eg mock video streaming) as well as an octoprint container for python2 and python3. However, to enable interactive debugging the plugin containers are not running the plugins yet.

In another terminal:

To install the plugin in the container run:
```bash
docker compose exec {op/op_python2} octoprint dev plugin:install
```

Then to start octoprint (and by extension the plugin) run:

```bash
docker compose exec {op/op_python2} ./start.sh
```
