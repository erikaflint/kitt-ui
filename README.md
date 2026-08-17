# KITT UI V1

Local operator UI for the KITT V1.2 shared runtime.

## Start

```sh
cd /Users/erikaflint/code/kitt-ui
make run
```

Open:

```text
http://127.0.0.1:8776/
```

## Stop

Press `Ctrl-C` in the terminal running `make run`.

## Logs

```text
/Users/erikaflint/code/kitt-ui/outputs/kitt-ui/kitt-ui.log
```

Logs must not contain runtime tokens or `.env` values.

## Runtime Boundary

The server binds to `127.0.0.1` by default.

Browser JavaScript talks only to this local server:

```text
/api/health
/api/jobs
/api/jobs/:id
/api/packets
```

The local server talks to the KITT Runtime API with server-side credentials.

By default, the standalone UI reads runtime credentials from the sibling
operating-system worker file:

```text
/Users/erikaflint/code/chc-ai-operating-system/cloudflare/kitt-runtime-worker/.dev.vars
```

To use a different file, set:

```sh
KITT_UI_ENV_FILE=/path/to/.dev.vars make run
```

## Recovery

If the UI says runtime is unauthorized, check:

```sh
cd /Users/erikaflint/code/chc-ai-operating-system
make runtime-health
```

If the job board is empty, use the same command and then:

```sh
make runtime-jobs
```

If the UI does not start, check the log file above and confirm port `8776` is not already in use.

## Sandbox

Temporary experiments for this app go in:

```text
/Users/erikaflint/code/kitt-ui/sandbox/
```

Promote it or lose it. Sandbox is not memory and not production.

## Packets

KITT UI can render structured worker packets from:

```text
/Users/erikaflint/code/kitt-ui/packets/active/
/Users/erikaflint/code/kitt-ui/packets/samples/
```

The first packet contract is calendar intelligence:

```text
/Users/erikaflint/code/kitt-ui/docs/packets.md
```

Workers should put live operator packets in `packets/active/`. Sample contracts
belong in `packets/samples/`.
