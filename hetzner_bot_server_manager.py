import asyncio
import logging
from hetzner_api import hetzner_api

logger = logging.getLogger(__name__)

async def reset_server_traffic(server_id):
    logs = []
    
    try:
        logs.append(("📥", "Fetching server information..."))
        server = await hetzner_api.get_server(server_id)
        
        if not server:
            logs.append(("❌", "Failed to fetch server information"))
            return False, logs
        
        current_status = server.get('status')
        current_type = server.get('server_type', {}).get('name')
        
        if not current_type:
            logs.append(("❌", "Could not determine current server type"))
            return False, logs
        
        logs.append(("💾", f"Current plan: {current_type}"))
        
        server_types = await hetzner_api.get_server_types()
        if not server_types:
            logs.append(("❌", "Failed to fetch available server types"))
            return False, logs
        
        current_type_info = next((t for t in server_types if t['name'] == current_type), None)
        if not current_type_info:
            logs.append(("❌", f"Current type {current_type} not found in available types"))
            return False, logs
        
        current_cores = current_type_info.get('cores', 0)
        current_memory = current_type_info.get('memory', 0)
        
        upgrade_type = None
        for st in sorted(server_types, key=lambda x: x.get('cores', 0)):
            if (st.get('cores', 0) > current_cores or 
                st.get('memory', 0) > current_memory):
                upgrade_type = st['name']
                break
        
        if not upgrade_type:
            logs.append(("❌", "No upgrade plan available"))
            return False, logs
        
        logs.append(("🔼", f"Upgrade plan selected: {upgrade_type}"))
        
        if current_status == "running":
            logs.append(("🔴", "Shutting down server..."))
            await hetzner_api.power_off(server_id)
            
            if not await hetzner_api.wait_for_status(server_id, "off", max_attempts=40):
                logs.append(("❌", "Server failed to shutdown"))
                return False, logs
            
            logs.append(("✅", "Server is now OFF"))
            await asyncio.sleep(2)
        
        logs.append(("🔼", f"Upgrading to {upgrade_type}..."))
        result = await hetzner_api.change_server_type(server_id, upgrade_type, upgrade_disk=False)
        
        if not result:
            logs.append(("❌", "Upgrade request failed"))
            return False, logs
        
        await asyncio.sleep(5)
        
        logs.append(("⏳", "Waiting for upgrade to complete..."))
        for i in range(30):
            server = await hetzner_api.get_server(server_id)
            if server and server.get('server_type', {}).get('name') == upgrade_type:
                logs.append(("✅", "Upgrade completed successfully"))
                break
            await asyncio.sleep(5)
            if (i + 1) % 6 == 0:
                logs.append(("⏳", f"Still upgrading... ({(i+1)*5}s elapsed)"))
        
        await asyncio.sleep(3)
        
        logs.append(("🟢", "Starting server..."))
        await hetzner_api.power_on(server_id)
        
        if not await hetzner_api.wait_for_status(server_id, "running", max_attempts=40):
            logs.append(("⚠️", "Server started but status check timed out"))
        else:
            logs.append(("✅", "Server is now RUNNING"))
        
        await asyncio.sleep(5)
        
        logs.append(("🔽", f"Downgrading back to {current_type}..."))
        await hetzner_api.power_off(server_id)
        
        if not await hetzner_api.wait_for_status(server_id, "off", max_attempts=40):
            logs.append(("❌", "Failed to shutdown for downgrade"))
            return False, logs
        
        await asyncio.sleep(2)
        
        result = await hetzner_api.change_server_type(server_id, current_type, upgrade_disk=False)
        
        if not result:
            logs.append(("❌", "Downgrade request failed"))
            return False, logs
        
        await asyncio.sleep(5)
        
        logs.append(("⏳", "Waiting for downgrade to complete..."))
        for i in range(30):
            server = await hetzner_api.get_server(server_id)
            if server and server.get('server_type', {}).get('name') == current_type:
                logs.append(("✅", "Downgrade completed successfully"))
                break
            await asyncio.sleep(5)
            if (i + 1) % 6 == 0:
                logs.append(("⏳", f"Still downgrading... ({(i+1)*5}s elapsed)"))
        
        await asyncio.sleep(3)
        
        logs.append(("🟢", "Starting server with original plan..."))
        await hetzner_api.power_on(server_id)
        
        if not await hetzner_api.wait_for_status(server_id, "running", max_attempts=40):
            logs.append(("⚠️", "Server started but status check timed out"))
        else:
            logs.append(("✅", "Server is now RUNNING"))
        
        logs.append(("🎉", "Traffic reset process completed!"))
        return True, logs
        
    except Exception as e:
        logger.error(f"Error during traffic reset: {e}")
        logs.append(("❌", f"Unexpected error: {str(e)}"))
        return False, logs