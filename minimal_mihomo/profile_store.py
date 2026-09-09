from datetime import datetime, timezone
from pathlib import Path
import uuid
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.parse import urlsplit
from .runtime_builder import MAX_PROFILE_BYTES, parse
from .storage import ControllerError, atomic_write, read_json, write_json


def now():
    return datetime.now(timezone.utc).isoformat()


class SecureRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != 'https':
            raise ControllerError('Subscription redirected to insecure HTTP.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str) -> bytes:
    if urlsplit(url).scheme != 'https':
        raise ControllerError('Subscription URLs must use HTTPS to protect credentials.')
    try:
        request = Request(url, headers={'User-Agent': 'MinimalMihomo/0.1', 'Accept': 'application/yaml,text/yaml,*/*'})
        # Never inherit shell proxy settings; this request uses the OS route.
        with build_opener(ProxyHandler({}), SecureRedirect()).open(request, timeout=30) as response:
            if urlsplit(response.url).scheme != 'https':
                raise ControllerError('Subscription redirected to insecure HTTP.')
            data = response.read(MAX_PROFILE_BYTES + 1)
    except ControllerError:
        raise
    except Exception:
        raise ControllerError('Subscription download failed; URL and response withheld to protect secrets.') from None
    parse(data)
    return data


class ProfileStore:
    def __init__(self, paths):
        self.paths = paths

    def all(self):
        return read_json(self.paths.profiles, {})

    def get(self, profile_id):
        try:
            return self.all()[profile_id]
        except KeyError:
            raise ControllerError('Unknown profile ID.') from None

    def _save(self, profile_id, profile):
        profiles = self.all()
        profiles[profile_id] = profile
        write_json(self.paths.profiles, profiles)

    def add_yaml(self, path: Path, name=None):
        path = path.expanduser().resolve(strict=True)
        parse(path.read_bytes())
        if path.is_relative_to(self.paths.data.resolve()):
            raise ControllerError('Local source must be outside controller-owned storage.')
        profile_id = uuid.uuid4().hex[:12]
        self._save(profile_id, {'name': name or path.stem, 'kind': 'local', 'path': str(path)})
        return profile_id

    def add_subscription(self, url, validate, name=None):
        data = download(url)
        validate(data, None)
        profile_id = uuid.uuid4().hex[:12]
        path = self._snapshot(profile_id, data)
        self._save(profile_id, {'name': name or 'Subscription', 'kind': 'subscription',
                               'url': url, 'path': str(path), 'refreshed': now()})
        return profile_id

    def _snapshot(self, profile_id, data):
        path = self.paths.data / 'subscriptions' / profile_id / (uuid.uuid4().hex + '.yaml')
        atomic_write(path, data)
        path.chmod(0o400)
        return path

    def refresh(self, profile_id, validate):
        profile = self.get(profile_id)
        if profile['kind'] != 'subscription':
            raise ControllerError('This profile is not a subscription.')
        data = download(profile['url'])
        validate(data, None)
        path = self._snapshot(profile_id, data)
        profile.update(path=str(path), refreshed=now())
        self._save(profile_id, profile)

    def duplicate(self, profile_id, name):
        profile = dict(self.get(profile_id))
        profile['name'] = name
        new_id = uuid.uuid4().hex[:12]
        self._save(new_id, profile)
        return new_id

    def rename(self, profile_id, name):
        name = name.strip()
        if not name:
            raise ControllerError('Profile name cannot be empty.')
        profile = dict(self.get(profile_id))
        profile['name'] = name
        self._save(profile_id, profile)

    def remove(self, profile_id):
        profiles = self.all()
        if profile_id not in profiles:
            raise ControllerError('Unknown profile ID.')
        state = read_json(self.paths.state, {})
        if profile_id == state.get('profile_id'):
            raise ControllerError('Cannot remove the active last-good profile.')
        del profiles[profile_id]
        write_json(self.paths.profiles, profiles)
