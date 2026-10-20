- `af_bootstrap.sh` reloads systemd only when a unit file it manages changed, and on GPU
  hosts warns that a reload strips GPU access from containers on the legacy NVIDIA device
  request. The host setup (contracts §11) asks GPU containers to use `runtime: nvidia`.
  A rerun had reloaded systemd every time, and one reload cut a GPU host's inference
  containers off from the GPU.
