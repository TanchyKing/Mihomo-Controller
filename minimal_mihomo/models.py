from dataclasses import asdict, dataclass, field
import ipaddress
import secrets
from .storage import ControllerError, read_json, write_json


@dataclass
class Settings:
    tun: bool = True
    stack: str = 'gvisor'
    auto_route: bool = True
    auto_redirect: bool = False
    strict_route: bool = False
    auto_detect_interface: bool = True
    interface: str = ''
    dns_hijack: list[str] = field(default_factory=lambda: ['any:53'])
    secure_dns: bool = True
    mtu: int = 1500
    route_exclude_address: list[str] = field(default_factory=list)
    mode: str = 'source'
    ipv6: bool | None = None
    mixed_port: int = 17890
    api_port: int = 19099
    api_secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))

    def validate(self):
        for key in ('tun', 'auto_route', 'auto_redirect', 'strict_route', 'auto_detect_interface', 'secure_dns'):
            if type(getattr(self, key)) is not bool:
                raise ControllerError(f'{key} must be boolean.')
        if self.ipv6 is not None and type(self.ipv6) is not bool:
            raise ControllerError('ipv6 must be true, false, or null (preserve source).')
        if self.stack not in ('gvisor', 'system', 'mixed') or self.mode not in ('source', 'rule', 'global', 'direct'):
            raise ControllerError('Invalid stack or mode.')
        if type(self.mtu) is not int or not 1280 <= self.mtu <= 9000:
            raise ControllerError('MTU must be between 1280 and 9000.')
        if not isinstance(self.interface, str) or any(c.isspace() for c in self.interface):
            raise ControllerError('Invalid physical interface name.')
        for port in (self.mixed_port, self.api_port):
            if type(port) is not int or not 1024 <= port <= 65535:
                raise ControllerError('Ports must be integers between 1024 and 65535.')
        if self.mixed_port == self.api_port:
            raise ControllerError('Mixed and API ports must differ.')
        for key in ('dns_hijack', 'route_exclude_address'):
            value = getattr(self, key)
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ControllerError(f'{key} must be a JSON list of strings.')
        try:
            for address in self.route_exclude_address:
                ipaddress.ip_network(address, strict=False)
        except ValueError:
            raise ControllerError('Invalid route exclusion CIDR.') from None
        if not isinstance(self.api_secret, str) or len(self.api_secret) < 24:
            raise ControllerError('API secret must be at least 24 characters.')

    @classmethod
    def load(cls, paths):
        result = cls(**read_json(paths.settings, {}))
        result.validate()
        if not paths.settings.exists():
            write_json(paths.settings, asdict(result))
        return result
