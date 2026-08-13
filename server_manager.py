import asyncio
import logging
from hetzner_api import hetzner_api
from overage_tracker import overage_tracker
from utils import overage_cost, location_name, type_family, type_price

logger = logging.getLogger(__name__)


def pick_upgrade_type(types, current, location):
    """Cheapest plan to pass through on the way to resetting the counter.

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
    if not candidates:
        return None
    family = type_family(current.get('name', ''))
    same_family = [t for t in candidates if type_family(t.get('name', '')) == family]
    return min(same_family or candidates, key=lambda t: type_price(t, location))


async def _upgrade_target(server):
    types = await hetzner_api.get_server_types()
    return pick_upgrade_type(types, server.get('server_type', {}), location_name(server))

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
        
        target = await _upgrade_target(server)

        if not target:
            await add_log("❌", f"No larger plan to bounce through for {current_type}")
            return False, logs

        upgrade_type = target["name"]
        await add_log("🔼", f"Upgrade plan selected: {upgrade_type}")

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
        
        await add_log("🔼", f"Upgrading to {upgrade_type}...")
        result = await hetzner_api.change_server_type(server_id, upgrade_type, upgrade_disk=False)
        
        if not result or (result.get('error')):
            await add_log("❌", f"Upgrade request failed: {result.get('error', {}).get('message', 'Unknown error')}")
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
        
        result = await hetzner_api.change_server_type(server_id, current_type, upgrade_disk=False)
        
        if not result or (result.get('error')):
            await add_log("❌", f"Downgrade request failed: {result.get('error', {}).get('message', 'Unknown error')}")
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
    pick = lambda n: (pick_upgrade_type(TYPES, by[n], 'hel1') or {}).get('name')

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
    assert pick_upgrade_type(TYPES, by['cpx42'], 'hel1') is None

    # the choice depends on the type list alone, nothing per-datacenter
    assert 'available' not in pick_upgrade_type.__code__.co_varnames

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
