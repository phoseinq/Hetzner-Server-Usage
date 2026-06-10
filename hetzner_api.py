import aiohttp
import asyncio
import logging
from config import Config

logger = logging.getLogger(__name__)

class HetznerAPI:
    def __init__(self):
        self.base_url = Config.HETZNER_API_BASE
        self.headers = {
            'Authorization': f'Bearer {Config.HETZNER_API_TOKEN}',
            'Content-Type': 'application/json'
        }

    async def _request(self, method, endpoint, data=None, retry=3):
        url = f"{self.base_url}{endpoint}"
        for attempt in range(retry):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.request(
                        method, url, headers=self.headers, json=data,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        if response.status == 429:
                            wait_time = min(2 ** attempt * 5, 60)
                            logger.warning(f"Rate limited. Waiting {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue
                        try:
                            result = await response.json()
                        except Exception:
                            # DELETE returns 204 with an empty body
                            result = {}
                        if response.status >= 400:
                            logger.error(f"API Error {response.status}: {result}")
                            return None
                        return result if result is not None else {}
            except Exception as e:
                logger.error(f"Request failed (attempt {attempt + 1}): {e}")
                if attempt < retry - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return None
        return None

    async def list_servers(self):
        result = await self._request('GET', '/servers')
        return result.get('servers', []) if result else []

    async def get_server(self, server_id):
        result = await self._request('GET', f'/servers/{server_id}')
        return result.get('server') if result else None

    async def power_off(self, server_id):
        return await self._request('POST', f'/servers/{server_id}/actions/poweroff')

    async def power_on(self, server_id):
        return await self._request('POST', f'/servers/{server_id}/actions/poweron')

    async def change_server_type(self, server_id, server_type, upgrade_disk=False):
        return await self._request('POST', f'/servers/{server_id}/actions/change_type', {
            'server_type': server_type,
            'upgrade_disk': upgrade_disk
        })

    async def get_server_types(self):
        result = await self._request('GET', '/server_types')
        return result.get('server_types', []) if result else []

    async def reset_password(self, server_id):
        return await self._request('POST', f'/servers/{server_id}/actions/reset_password')

    async def list_images(self, image_type='snapshot'):
        result = await self._request('GET', f'/images?type={image_type}&per_page=50')
        return result.get('images', []) if result else []

    async def get_image(self, image_id):
        result = await self._request('GET', f'/images/{image_id}')
        return result.get('image') if result else None

    async def create_snapshot(self, server_id, description):
        return await self._request('POST', f'/servers/{server_id}/actions/create_image', {
            'type': 'snapshot',
            'description': description,
        })

    async def delete_image(self, image_id):
        # returns {} on success (204), None on failure
        return await self._request('DELETE', f'/images/{image_id}')

    async def list_floating_ips(self):
        result = await self._request('GET', '/floating_ips?per_page=50')
        return result.get('floating_ips', []) if result else []

    async def list_primary_ips(self):
        result = await self._request('GET', '/primary_ips?per_page=50')
        return result.get('primary_ips', []) if result else []

    async def get_pricing(self):
        result = await self._request('GET', '/pricing')
        return result.get('pricing', {}) if result else {}

    async def wait_for_status(self, server_id, target_status, max_attempts=40):
        for i in range(max_attempts):
            server = await self.get_server(server_id)
            if server and server.get('status') == target_status:
                logger.info(f"Server {server_id} reached status: {target_status}")
                return True
            await asyncio.sleep(5)
        logger.warning(f"Server {server_id} did not reach {target_status} in time")
        return False

hetzner_api = HetznerAPI()
