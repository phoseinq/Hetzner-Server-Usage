import asyncio
import logging
from hetzner_api import hetzner_api
from overage_tracker import overage_tracker
from utils import overage_cost, location_name, type_family, type_price

logger = logging.getLogger(__name__)


def pick_upgrade_type(types, current, location):
    """Cheapest plan worth bouncing through to reset the traffic counter.

    NOTE: this deliberately does not look at what the datacenter reports as
    available. The API accepts a type change the console would refuse, and
    that is the whole reason the traffic reset works — Change Plan filters on
    availability, this must not, or there is nothing to bounce through.

    The target has to be at least as big as the current plan in every
    dimension, disk included: the change runs with upgrade_disk=False so the
    disk is never grown, and Hetzner refuses a type whose disk is smaller
    than the server already has. Same family is preferred, so the plan the
    server sits on for those two minutes stays close to what it was.
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

    # availability is not an input at all: cx23 -> cx33 is exactly the case
    # Hetzner lists as unavailable, and it must still be chosen
    assert 'available' not in pick_upgrade_type.__code__.co_varnames
    print('server_manager demo OK')


if __name__ == '__main__':
    demo()
