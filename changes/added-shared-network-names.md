- Deployment contracts: a service that joins another project's network reaches its own
  services through project-unique aliases, because Docker may resolve a name both
  networks carry to the other project's container (a production migration connected to
  the gateway's `db`); rehearsals use a stand-in network with the real peer names.
