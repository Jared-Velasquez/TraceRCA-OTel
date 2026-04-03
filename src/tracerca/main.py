# window algorithm here; collect batches of independent spans from
# Redis Streams; convert to Invocations and construct InternalTraces.

# Once the cron job / scheduler triggers, perform the above and then run
# the TraceRCA algorithm pipeline