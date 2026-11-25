import asyncio
import logging
from .hetzner_api import hetzner_api
from .overage_tracker import overage_tracker
from .utils import overage_cost, location_name, type_family, type_price

logger = logging.getLogger(__name__)

# Coming back there is only one plan that will do, so a datacenter with no room
# for it is waited out rather than worked around.
DOWNGRADE_ATTEMPTS = 6
DOWNGRADE_GAP = 30


def pick_upgrade_types(types, current, location):
    """Plans to pass through on the way to resetting the counter, cheapest first.

    The target has to be at least as big as the current plan in every
    dimension, disk included: the change runs with upgrade_disk=False so the
    disk is never grown, and a type whose disk is smaller than the server
    already has is not a valid target. Same family is preferred, so the plan
    the server sits on for those two minutes stays close to what it was.
    """
    cores, memory, disk = (current.get(k, 0) or 0 for k in ('cores', 'memory', 'disk'))

    def bigger(t):
        return (
            (t.get('cores', 0) or 0) >= cores
            and (t.get('memory', 0) or 0) >= memory
            and (t.get('disk', 0) or 0) >= disk
            and ((t.get('cores', 0) or 0, t.get('memory', 0) or 0) != (cores, memory))
        )

    candidates = [
        t for t in types
        if t.get('name') != current.get('name')
        and t.get('architecture') == current.get('architecture')
        and not t.get('deprecation')
        and bigger(t)
    ]
    family = type_family(current.get('name', ''))
    candidates.sort(key=lambda t: (type_family(t.get('name', '')) != family,
                                   type_price(t, location)))
    return candidates


async def _upgrade_targets(server):
    types = await hetzner_api.get_server_types()
    return pick_upgrade_types(types, server.get('server_type', {}), location_name(server))


def _api_error(default='Unknown error'):
    """Why the last call failed. _request returns None, the reason lands here."""
    return (getattr(hetzner_api, 'last_error', None) or {}).get('message', default)


async def _start_again(server_id, was_running, add_log):
    """A failed resize must not leave the server sitting powered off."""
    if not was_running:
        return
    server = await hetzner_api.get_server(server_id, fresh=True)
    if server and server.get('status') == 'running':
        return
    await add_log("🟢", "Starting the server again...")
    await hetzner_api.power_on(server_id)
    if await hetzner_api.wait_for_status(server_id, "running", max_attempts=40):
        await add_log("✅", "Server is RUNNING again")
    else:
        await add_log("⚠️", "Could not confirm the server came back up -- check it")


async def reset_server_traffic(server_id, progress_callback=None):
    logs = []
    
    async def add_log(emoji, message):
        logs.append((emoji, message))
        if progress_callback:
            await progress_callback(logs)
    
    try:
        await add_log("📥", "Fetching server information...")
        server = await hetzner_api.get_server(server_id)
        
        if not server:
            await add_log("❌", "Failed to fetch server information")
            return False, logs
        
        current_status = server.get('status')
        current_type = server.get('server_type', {}).get('name')
        
        if not current_type:
            await add_log("❌", "Could not determine current server type")
            return False, logs
        
        await add_log("💾", f"Current plan: {current_type}")
        
        targets = await _upgrade_targets(server)

        if not targets:
            await add_log("❌", f"No larger plan to bounce through for {current_type}")
            return False, logs

        await add_log("🔼", f"Upgrade plan selected: {targets[0]['name']}")

        # resetting the counter is what stops Hetzner billing the overage, so
        # settle it now: it leaves this month's bill and is recorded as saved
        overage = overage_cost(server)
        if overage > 0:
            overage_tracker.update_live_overage(server_id, overage)
        owed = overage_tracker.get_server_month_overage(server_id)
        overage_tracker.commit_overage(server_id)
        if owed:
            await add_log("💰", f"€{owed:.2f} overage cleared from this month's bill")

        if current_status == "running":
            await add_log("🔴", "Shutting down server...")
            await hetzner_api.power_off(server_id)
            
            if not await hetzner_api.wait_for_status(server_id, "off", max_attempts=40):
                await add_log("❌", "Server failed to shutdown")
                return False, logs
            
            await add_log("✅", "Server is now OFF")
            await asyncio.sleep(2)
        
        # Hetzner refuses a resize into a plan its datacenter has no room for
        # (412 resource_unavailable), so walk down the list until one is taken.
        upgrade_type = None
        for candidate in targets:
            name = candidate["name"]
            await add_log("🔼", f"Upgrading to {name}...")
            if await hetzner_api.change_server_type(server_id, name, upgrade_disk=False):
                upgrade_type = name
                break
            await add_log("⚠️", f"{name} unavailable: {_api_error()}")

        if not upgrade_type:
            await add_log("❌", "No plan with free capacity to bounce through")
            await _start_again(server_id, current_status == "running", add_log)
            return False, logs
        
        await asyncio.sleep(5)
        
        await add_log("⏳", "Waiting for upgrade to complete...")
        for i in range(30):
            server = await hetzner_api.get_server(server_id, fresh=True)
            if server and server.get('server_type', {}).get('name') == upgrade_type:
                await add_log("✅", "Upgrade completed successfully")
                break
            await asyncio.sleep(5)
            if (i + 1) % 6 == 0:
                await add_log("⏳", f"Still upgrading... ({(i+1)*5}s elapsed)")
        
        await asyncio.sleep(3)
        
        await add_log("🟢", "Starting server...")
        await hetzner_api.power_on(server_id)
        
        if not await hetzner_api.wait_for_status(server_id, "running", max_attempts=40):
            await add_log("⚠️", "Server started but status check timed out")
        else:
            await add_log("✅", "Server is now RUNNING")
        
        await asyncio.sleep(5)
        
        await add_log("🔽", f"Downgrading back to {current_type}...")
        await hetzner_api.power_off(server_id)
        
        if not await hetzner_api.wait_for_status(server_id, "off", max_attempts=40):
            await add_log("❌", "Failed to shutdown for downgrade")
            return False, logs
        
        await asyncio.sleep(2)
        
        result = None
        for attempt in range(1, DOWNGRADE_ATTEMPTS + 1):
            result = await hetzner_api.change_server_type(server_id, current_type, upgrade_disk=False)
            if result:
                break
            await add_log("⚠️", f"{current_type} refused "
                                f"({attempt}/{DOWNGRADE_ATTEMPTS}): {_api_error()}")
            if attempt < DOWNGRADE_ATTEMPTS:
                await asyncio.sleep(DOWNGRADE_GAP)

        if not result:
            await add_log("❌", f"Could not get back to {current_type} — "
                                f"the server is left on {upgrade_type}, retry later")
            await _start_again(server_id, True, add_log)
            return False, logs
        
        await asyncio.sleep(5)
        
        await add_log("⏳", "Waiting for downgrade to complete...")
        for i in range(30):
            server = await hetzner_api.get_server(server_id, fresh=True)
            if server and server.get('server_type', {}).get('name') == current_type:
                await add_log("✅", "Downgrade completed successfully")
                break
            await asyncio.sleep(5)
            if (i + 1) % 6 == 0:
                await add_log("⏳", f"Still downgrading... ({(i+1)*5}s elapsed)")
        
        await asyncio.sleep(3)
        
        await add_log("🟢", "Starting server with original plan...")
        await hetzner_api.power_on(server_id)
        
        if not await hetzner_api.wait_for_status(server_id, "running", max_attempts=40):
            await add_log("⚠️", "Server started but status check timed out")
        else:
            await add_log("✅", "Server is now RUNNING")
        
        await add_log("🎉", "Traffic reset process completed!")
        return True, logs
        
    except Exception as e:
        logger.error(f"Error during traffic reset: {e}")
        await add_log("❌", f"Unexpected error: {str(e)}")
        try:
            await _start_again(server_id, True, add_log)
        except Exception as recover_error:
            logger.error(f"Could not start the server again: {recover_error}")
        return False, logs


async def swap_primary_ip(server_id, new_ip_id, api=None, progress_callback=None):
    """Put a different primary IP on a server.

    Hetzner refuses both halves of this while the server runs, so it is powered
    off and — only if it was running to begin with — started again afterwards.
    The IP that comes off is left unassigned rather than deleted.

    If attaching the new IP fails, the old one goes back on and the server is
    started again, so it never ends up running without the IP it had.
    """
    api = api or hetzner_api
    logs = []

    async def add_log(emoji, message):
        logs.append((emoji, message))
        if progress_callback:
            await progress_callback(logs)

    try:
        server = await api.get_server(server_id, fresh=True)
        if not server:
            await add_log("❌", "Failed to fetch server information")
            return False, logs

        name = server.get('name', 'Server')
        was_running = server.get('status') == 'running'
        old = (server.get('public_net') or {}).get('ipv4') or {}
        old_id, old_ip = old.get('id'), old.get('ip')

        if was_running:
            await add_log("🔴", f"Shutting down {name}...")
            await api.power_off(server_id)
            if not await api.wait_for_status(server_id, "off", max_attempts=40):
                await add_log("❌", "Server failed to shut down — nothing was changed")
                return False, logs
            await add_log("✅", "Server is now OFF")
        else:
            await add_log("💤", "Server is already off")

        if old_id:
            await add_log("✂️", f"Removing {old_ip}...")
            if await api.unassign_primary_ip(old_id) is None:
                await add_log("❌", f"Could not remove {old_ip}")
                if was_running:
                    await api.power_on(server_id)
                return False, logs
            await add_log("✅", f"{old_ip} is now free")

        await add_log("📎", "Attaching the new IP...")
        if await api.assign_primary_ip(new_ip_id, server_id) is None:
            await add_log("❌", "Could not attach the new IP — putting the old one back")
            if old_id:
                # the new IP never went on, so only the old one needs restoring
                if await api.assign_primary_ip(old_id, server_id) is not None:
                    await add_log("↩️", f"{old_ip} is back on the server")
                else:
                    await add_log("⚠️", f"{old_ip} could NOT be put back — the server has no IPv4")
            if was_running:
                await api.power_on(server_id)
                await add_log("🟢", "Server started again")
            return False, logs

        if was_running:
            await add_log("🟢", "Starting server...")
            await api.power_on(server_id)
            if not await api.wait_for_status(server_id, "running", max_attempts=40):
                await add_log("⚠️", "Server started but the status check timed out")
            else:
                await add_log("✅", "Server is now RUNNING")

        await add_log("🎉", "IP swap completed!")
        return True, logs

    except Exception as e:
        logger.error(f"Error during IP swap: {e}")
        await add_log("❌", f"Unexpected error: {str(e)}")
        return False, logs


async def detach_primary_ip(server_id, pip_id, api=None, progress_callback=None):
    """Take a primary IP off a server, leaving it without a public IPv4."""
    api = api or hetzner_api
    logs = []

    async def add_log(emoji, message):
        logs.append((emoji, message))
        if progress_callback:
            await progress_callback(logs)

    try:
        server = await api.get_server(server_id, fresh=True)
        if not server:
            await add_log("❌", "Failed to fetch server information")
            return False, logs
        was_running = server.get('status') == 'running'

        if was_running:
            await add_log("🔴", f"Shutting down {server.get('name', 'Server')}...")
            await api.power_off(server_id)
            if not await api.wait_for_status(server_id, "off", max_attempts=40):
                await add_log("❌", "Server failed to shut down — nothing was changed")
                return False, logs
            await add_log("✅", "Server is now OFF")

        await add_log("✂️", "Removing the IP...")
        if await api.unassign_primary_ip(pip_id) is None:
            await add_log("❌", "Could not remove the IP")
            if was_running:
                await api.power_on(server_id)
            return False, logs

        if was_running:
            await add_log("🟢", "Starting server...")
            await api.power_on(server_id)
            await api.wait_for_status(server_id, "running", max_attempts=40)
        await add_log("🎉", "The IP is now free. The server has no public IPv4.")
        return True, logs

    except Exception as e:
        logger.error(f"Error during IP detach: {e}")
        await add_log("❌", f"Unexpected error: {str(e)}")
        return False, logs


def demo():
    def t(name, cores, mem, disk, price, arch='x86', dep=None):
        return {'name': name, 'cores': cores, 'memory': mem, 'disk': disk,
                'architecture': arch, 'deprecation': dep,
                'prices': [{'location': 'hel1', 'price_monthly': {'net': str(price)}}]}

    TYPES = [
        t('cx23', 2, 4, 40, 5.49),   t('cx33', 4, 8, 80, 13.10),  t('cx43', 8, 16, 160, 26.10),
        t('cpx22', 3, 4, 80, 19.49), t('cpx32', 4, 8, 160, 35.49), t('cpx42', 8, 16, 240, 60.49),
        t('cax11', 2, 4, 40, 3.79, arch='arm'), t('cax21', 4, 8, 80, 6.49, arch='arm'),
        t('old99', 16, 32, 360, 1.00, dep={'announced': 'x'}),
    ]
    by = {x['name']: x for x in TYPES}
    picks = lambda n: [t['name'] for t in pick_upgrade_types(TYPES, by[n], 'hel1')]
    pick = lambda n: (picks(n) or [None])[0]

    # the family that could not reset at all before
    assert pick('cpx22') == 'cpx32', pick('cpx22')
    assert pick('cpx32') == 'cpx42'
    # cx and cax still work, and stay inside their own family
    assert pick('cx23') == 'cx33'
    assert pick('cax11') == 'cax21'
    # never a smaller plan, never a smaller disk, never a deprecated one
    for name in ('cpx22', 'cx23', 'cax11'):
        chosen = by[pick(name)]
        cur = by[name]
        assert chosen['cores'] >= cur['cores'] and chosen['memory'] >= cur['memory']
        assert chosen['disk'] >= cur['disk'], f"{name}: disk would shrink"
        assert not chosen['deprecation']
        assert chosen['architecture'] == cur['architecture']
    # the cheapest step up, not just any
    assert pick('cx23') != 'cx43'
    # nothing bigger => the caller is told, rather than picking something wrong
    assert pick_upgrade_types(TYPES, by['cpx42'], 'hel1') == []

    # the choice depends on the type list alone, nothing per-datacenter
    assert 'available' not in pick_upgrade_types.__code__.co_varnames

    # a datacenter with no room for the first plan is not the end of the run:
    # the rest follow, AMD included, with the same family still tried first
    assert picks('cx33') == ['cx43', 'cpx42'], picks('cx33')
    assert picks('cpx22') == ['cpx32', 'cpx42', 'cx33', 'cx43'], picks('cpx22')
    for name in ('cx33', 'cpx22', 'cax11'):
        cur = by[name]
        for chosen in (by[c] for c in picks(name)):
            assert chosen['disk'] >= cur['disk'], f"{name}: disk would shrink"
            assert chosen['architecture'] == cur['architecture']
            assert not chosen['deprecation']

    _swap_demo()
    print('server_manager demo OK')


class _StubAPI:
    """Enough of the API to exercise the swap without touching a real server."""

    def __init__(self, status='running', old_ip_id=55, fail=None):
        self.status = status
        self.old_ip_id = old_ip_id
        self.fail = fail or set()      # method names that should return None
        self.calls = []
        self.assigned = old_ip_id

    async def get_server(self, sid, fresh=False):
        return {'id': sid, 'name': 'srv', 'status': self.status,
                'public_net': {'ipv4': {'id': self.old_ip_id, 'ip': '1.2.3.4'}}
                if self.old_ip_id else {'ipv4': None}}

    async def power_off(self, sid):
        self.calls.append('power_off'); self.status = 'off'; return {}

    async def power_on(self, sid):
        self.calls.append('power_on'); self.status = 'running'; return {}

    async def wait_for_status(self, sid, status, max_attempts=40):
        return 'wait_fail' not in self.fail

    async def unassign_primary_ip(self, pid):
        self.calls.append(f'unassign:{pid}')
        if 'unassign' in self.fail:
            return None
        self.assigned = None
        return {}

    async def assign_primary_ip(self, pid, sid):
        self.calls.append(f'assign:{pid}')
        if 'assign' in self.fail and pid != self.old_ip_id:
            return None
        self.assigned = pid
        return {}


def _swap_demo():
    import asyncio

    # happy path: off, old IP removed, new one attached, started again
    api = _StubAPI()
    ok, _ = asyncio.run(swap_primary_ip(1, 99, api=api))
    assert ok and api.calls == ['power_off', 'unassign:55', 'assign:99', 'power_on'], api.calls
    assert api.assigned == 99 and api.status == 'running'

    # a server that was already off must not be started
    api = _StubAPI(status='off')
    ok, _ = asyncio.run(swap_primary_ip(1, 99, api=api))
    assert ok and 'power_on' not in api.calls and api.status == 'off', api.calls

    # a failed attach puts the old IP back and starts the server again
    api = _StubAPI(fail={'assign'})
    ok, logs = asyncio.run(swap_primary_ip(1, 99, api=api))
    assert not ok
    assert api.calls == ['power_off', 'unassign:55', 'assign:99', 'assign:55', 'power_on'], api.calls
    assert api.assigned == 55, "the server was left without its original IP"
    assert any('back on the server' in m for _, m in logs)

    # if it will not power off, nothing is touched at all
    api = _StubAPI(fail={'wait_fail'})
    ok, _ = asyncio.run(swap_primary_ip(1, 99, api=api))
    assert not ok and api.calls == ['power_off'], api.calls

    # a server with no IPv4 yet: nothing to remove, just attach
    api = _StubAPI(old_ip_id=None)
    ok, _ = asyncio.run(swap_primary_ip(1, 99, api=api))
    assert ok and api.calls == ['power_off', 'assign:99', 'power_on'], api.calls

    # detach leaves the server on, without an IP
    api = _StubAPI()
    ok, _ = asyncio.run(detach_primary_ip(1, 55, api=api))
    assert ok and api.calls == ['power_off', 'unassign:55', 'power_on'], api.calls
    assert api.assigned is None


if __name__ == '__main__':
    demo()
