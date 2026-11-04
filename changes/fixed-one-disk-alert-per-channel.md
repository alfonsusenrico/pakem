- The monitor's disk alerts reach each channels file once. A host-wide `disk_low` or
  `disk_recovered` went out once per environment on the host, so a server with four
  environments sharing one channels file sent every alert four times. Servers take the fix
  when `pakem setup host` runs again.
