- Draft contracts v1: `health` and `[[routes]]` name a service and its container `port`
  instead of a placement port setting; the engine asks Docker which `127.0.0.1` port
  publishes it (a binding beyond loopback fails the health check). A route is
  `host_setting`, `service`, `port`.
