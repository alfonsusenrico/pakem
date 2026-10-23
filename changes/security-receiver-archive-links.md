- The release receiver no longer writes an archive entry through a symlink extracted
  earlier, refuses an entry given twice, and refuses any symlink that resolves outside the
  source tree once all are in place. Chained links (`p/q -> ..`, `r -> p/q/..`) could
  write anywhere the deploy user can. Run `pakem setup host <name>` to update each host.
