# Primary IP attach / detach — design

**Date:** 2026-08-13
**Status:** approved

## Problem

A primary IP can be created from the bot but never moved. To put a specific IP
on a server — one already written into DNS or a client config — you have to
leave the bot and use the Hetzner console.

## Goal

Attach a chosen primary IP to a server, and detach one, from inside the
**📍 Primary IPs** section. Not IP rotation: the point is putting a *particular*
IP on a server.

## What the API allows

Verified against the live API on 2026-08-13:

| Call | Result |
|---|---|
| `POST /primary_ips/{id}/actions/unassign` on a running server | `422 server_not_stopped` |
| `POST /primary_ips/{id}/actions/assign` on a running server | `422 server_not_stopped` |
| `POST /primary_ips` with `location` | works (`datacenter` is rejected) |

So the server has to be powered off for either half of a swap, and the bot has
to do that itself. `server_not_stopped` is checked before anything else, so a
location or type mismatch is not reported until the server is already off — the
picker has to filter for those up front rather than relying on the API to
complain.

## Screens

Entry is the Primary IPs list. Each row keeps its bulk-delete checkbox and gains
one action button:

```
[⬜ 128.140.73.105] [✂️ Detach]      assigned
[⬜ 2.28.51.137   ] [📎 Attach]      free
```

**Attach** lists only servers that can actually take the IP — same location,
same IP type — showing what each one runs on now. Choosing one leads to a
confirmation that names both IPs, says the server will be powered off, and gives
a rough downtime. Nothing happens before that confirmation.

**Detach** confirms separately and warns harder: the server ends up with no
public IPv4 and is unreachable until an IP is attached again.

After a successful creation, the result screen offers an attach button per new
IP, so the common path (create → put it on a server) does not require walking
back to the list.

## Flow

1. Note whether the server is running.
2. Power off, wait for `off`.
3. Unassign the server's current primary IP of that type, if it has one.
4. Assign the chosen IP.
5. Power back on — only if it was running to begin with.

Progress is streamed into the message as it goes, matching Reset Traffic.

## Failure handling

If the assign in step 4 fails, the old IP is re-assigned and the server is
started again, so it never ends up running without the IP it had. If that
recovery also fails, the log says plainly which IP is loose and which server is
off, rather than reporting success.

The old IP is always kept. It returns to the pool as unassigned and keeps
costing about €0.50/month net, which the confirmation screen states.

## Not doing

- Creating an IP inside the swap flow — creation already exists one screen away.
- Attaching from the server panel as well. One direction, one code path.
- Floating IPs. They already attach and detach without downtime and have their
  own screen.

## Tests

Against a stub API, so no real server is powered off:

- only same-location, same-type servers are offered
- the call order is power off → unassign → assign → power on
- a server that was already off is not started afterwards
- a failing assign restores the old IP and starts the server again
