import aiohttp
import asyncio
import logging
import time
from config import Config

logger = logging.getLogger(__name__)

# Hetzner allows 3600 requests/hour (refill 1/s). We stay far below that:
# requests are spaced out, GET responses are cached briefly, and slow-moving
# data (pricing, locations, server types, OS images) is cached for an hour.
MIN_REQUEST_INTERVAL = 0.4          # seconds between any two API requests
DEFAULT_GET_TTL = 10                # seconds; short cache for lists/details
LONG_TTL_PREFIXES = {
    '/pricing': 3600,
    '/locations': 3600,
    '/server_types': 3600,
    '/datacenters': 600,
    '/images?type=system': 3600,
}
RATE_LIMIT_SOFT_FLOOR = 200         # slow down when fewer requests remain


class HetznerAPI:
    def __init__(self):
        self.base_url = Config.HETZNER_API_BASE
        self.headers = {
            'Authorization': f'Bearer {Config.HETZNER_API_TOKEN}',
            'Content-Type': 'application/json'
        }
        self._throttle_lock = asyncio.Lock()
        self._last_request = 0.0
        self._cache = {}

    def _cache_get(self, endpoint):
        hit = self._cache.get(endpoint)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        return None

    def _cache_set(self, endpoint, result):
        ttl = DEFAULT_GET_TTL
        for prefix, long_ttl in LONG_TTL_PREFIXES.items():
            if endpoint.startswith(prefix):
                ttl = long_ttl
                break
        self._cache[endpoint] = (time.monotonic() + ttl, result)

    async def _throttle(self):
        async with self._throttle_lock:
            wait = MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def _request(self, method, endpoint, data=None, retry=3, fresh=False):
        if method == 'GET' and not fresh:
            cached = self._cache_get(endpoint)
            if cached is not None:
                return cached

        url = f"{self.base_url}{endpoint}"
        for attempt in range(retry):
            await self._throttle()
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
                        result = result if result is not None else {}
                        if method == 'GET':
                            self._cache_set(endpoint, result)
                        else:
                            # state changed: drop every cached response
                            self._cache.clear()
                        remaining = response.headers.get('RateLimit-Remaining')
                        if remaining and int(float(remaining)) < RATE_LIMIT_SOFT_FLOOR:
                            logger.warning(f"Rate limit low ({remaining} left), slowing down...")
                            await asyncio.sleep(3)
                        return result
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

    async def get_server(self, server_id, fresh=False):
        result = await self._request('GET', f'/servers/{server_id}', fresh=fresh)
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

    async def change_image_protection(self, image_id, delete_protect):
        return await self._request('POST', f'/images/{image_id}/actions/change_protection', {
            'delete': delete_protect,
        })

    async def list_floating_ips(self):
        result = await self._request('GET', '/floating_ips?per_page=50')
        return result.get('floating_ips', []) if result else []

    async def create_floating_ip(self, ip_type, home_location, name, description=None):
        return await self._request('POST', '/floating_ips', {
            'type': ip_type,
            'home_location': home_location,
            'name': name,
            'description': description or name,
        })

    async def delete_floating_ip(self, fip_id):
        # returns {} on success (204), None on failure
        return await self._request('DELETE', f'/floating_ips/{fip_id}')

    async def list_primary_ips(self):
        result = await self._request('GET', '/primary_ips?per_page=50')
        return result.get('primary_ips', []) if result else []

    async def create_primary_ip(self, ip_type, datacenter, name):
        return await self._request('POST', '/primary_ips', {
            'type': ip_type,
            'datacenter': datacenter,
            'name': name,
            'assignee_type': 'server',
            'auto_delete': False,
        })

    async def delete_primary_ip(self, pip_id):
        # returns {} on success (204), None on failure
        return await self._request('DELETE', f'/primary_ips/{pip_id}')

    async def list_locations(self):
        result = await self._request('GET', '/locations')
        return result.get('locations', []) if result else []

    async def list_datacenters(self):
        result = await self._request('GET', '/datacenters?per_page=50')
        return result.get('datacenters', []) if result else []

    async def get_datacenter(self, dc_name):
        result = await self._request('GET', f'/datacenters?name={dc_name}')
        dcs = result.get('datacenters', []) if result else []
        return dcs[0] if dcs else None

    async def rebuild_server(self, server_id, image):
        return await self._request('POST', f'/servers/{server_id}/actions/rebuild', {
            'image': image,
        })

    async def enable_backup(self, server_id):
        return await self._request('POST', f'/servers/{server_id}/actions/enable_backup')

    async def disable_backup(self, server_id):
        return await self._request('POST', f'/servers/{server_id}/actions/disable_backup')

    async def assign_floating_ip(self, fip_id, server_id):
        return await self._request('POST', f'/floating_ips/{fip_id}/actions/assign', {
            'server': server_id,
        })

    async def unassign_floating_ip(self, fip_id):
        return await self._request('POST', f'/floating_ips/{fip_id}/actions/unassign')

    async def list_volumes(self):
        result = await self._request('GET', '/volumes?per_page=50')
        return result.get('volumes', []) if result else []

    async def create_volume(self, name, size, server_id):
        return await self._request('POST', '/volumes', {
            'name': name,
            'size': size,
            'server': server_id,
            'automount': True,
            'format': 'ext4',
        })

    async def attach_volume(self, volume_id, server_id):
        return await self._request('POST', f'/volumes/{volume_id}/actions/attach', {
            'server': server_id,
            'automount': True,
        })

    async def detach_volume(self, volume_id):
        return await self._request('POST', f'/volumes/{volume_id}/actions/detach')

    async def delete_volume(self, volume_id):
        # returns {} on success (204), None on failure
        return await self._request('DELETE', f'/volumes/{volume_id}')

    async def get_pricing(self):
        result = await self._request('GET', '/pricing')
        return result.get('pricing', {}) if result else {}

    async def wait_for_status(self, server_id, target_status, max_attempts=40):
        for i in range(max_attempts):
            server = await self.get_server(server_id, fresh=True)
            if server and server.get('status') == target_status:
                logger.info(f"Server {server_id} reached status: {target_status}")
                return True
            await asyncio.sleep(5)
        logger.warning(f"Server {server_id} did not reach {target_status} in time")
        return False

hetzner_api = HetznerAPI()
