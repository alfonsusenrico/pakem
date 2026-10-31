- `pakem health` (experimental, ADR 0016): `check` reports the device's state and what it
  can shed, by tier (zero-impact or warm), with in-use flags from live sessions and builds,
  and `clean <id>...` removes only owner-approved findings, re-verified first. Named Docker
  volumes, `deps`, young `incremental` folders, agent folders, and `/tmp` are never
  findings. New core skill `device-health`. The weekly report gains a read-only health
  summary on a configured device.
