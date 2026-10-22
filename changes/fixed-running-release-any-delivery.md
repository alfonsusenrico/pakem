- `pakem release status | history | rollback` see the release an environment runs however it
  was delivered: a registry release from before the move to direct delivery showed as
  "running nothing", and `rollback previous` could not step back from it. The host reports
  it from its `current` release (after `pakem setup host` updates the receiver); until
  then the device reads it from the deployment record.
