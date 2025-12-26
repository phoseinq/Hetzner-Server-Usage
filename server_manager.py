import asyncio
import logging
from hetzner_api import hetzner_api

logger = logging.getLogger(__name__)

UPGRADE_MAP = {
    'cx23': 'cx33',
    'cx33': 'cx43',
    'cx43': 'cx53',
    'cax11': 'cax21',
    'cax21': 'cax31',
    'cax31': 'cax41',
}

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
        
        upgrade_type = UPGRADE_MAP.get(current_type)
        
        if not upgrade_type:
            await add_log("❌", f"No upgrade plan available for {current_type}")
            return False, logs
        
        await add_log("🔼", f"Upgrade plan selected: {upgrade_type}")
        
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
            server = await hetzner_api.get_server(server_id)
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
            server = await hetzner_api.get_server(server_id)
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
