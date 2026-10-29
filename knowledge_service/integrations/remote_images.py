"""Ingestion-only public image import with DNS validation and pinned connections."""
import http.client
import ipaddress
import socket
import ssl
import time
import threading
from concurrent.futures import ThreadPoolExecutor,TimeoutError as FutureTimeout
from urllib.parse import urlsplit,urljoin
from .document_media import inspect_image,MAX_IMAGE_BYTES,MAX_IMAGE_PIXELS


class RemoteImageError(ValueError):
    pass


_DNS_POOL=ThreadPoolExecutor(max_workers=4,thread_name_prefix='source-image-dns')
_DNS_SLOTS=threading.BoundedSemaphore(4)


def _bounded_resolve(resolver,host,port,timeout):
    if not _DNS_SLOTS.acquire(blocking=False):raise RemoteImageError('Image DNS resolver is busy')
    try:future=_DNS_POOL.submit(resolver,host,port)
    except BaseException:_DNS_SLOTS.release();raise
    future.add_done_callback(lambda completed:_DNS_SLOTS.release())
    try:return future.result(timeout=max(0,timeout))
    except FutureTimeout as exc:
        future.cancel()
        raise RemoteImageError('Remote image DNS timed out') from exc


def _resolve(host,port):
    return list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)))


def _public(address):
    ip=ipaddress.ip_address(address)
    # IPv4 embedded forms must satisfy the same policy as their IPv4 destination.
    if isinstance(ip,ipaddress.IPv6Address) and ip.ipv4_mapped: ip=ip.ipv4_mapped
    if isinstance(ip,ipaddress.IPv6Address) and (ip.sixtofour or ip.teredo or ip in ipaddress.ip_network('64:ff9b::/96')):return False
    return ip.is_global and not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _request(url,address,timeout,max_bytes):
    """Connect only to the validated IP; keep the origin host for Host and TLS SNI."""
    parsed=urlsplit(url);port=parsed.port or (443 if parsed.scheme=='https' else 80)
    deadline=time.monotonic()+timeout
    connection=http.client.HTTPConnection(parsed.hostname,port,timeout=timeout)
    active_socket=[None]
    def expire():
        sock=active_socket[0]
        if sock is not None:
            try:sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            sock.close()
    watchdog=threading.Timer(timeout,expire);watchdog.daemon=True;watchdog.start()
    try:
        connection.sock=socket.create_connection((address,port),timeout=timeout)
        active_socket[0]=connection.sock
        if time.monotonic()>=deadline:raise RemoteImageError('Remote image timed out')
        if parsed.scheme=='https':
            connection.sock=ssl.create_default_context().wrap_socket(connection.sock,server_hostname=parsed.hostname,do_handshake_on_connect=False)
            active_socket[0]=connection.sock
            if time.monotonic()>=deadline:raise RemoteImageError('Remote image timed out')
            connection.sock.settimeout(max(.001,deadline-time.monotonic()))
            connection.sock.do_handshake()
        connection.request('GET',parsed.path or '/' if not parsed.query else (parsed.path or '/')+'?'+parsed.query,
                           headers={'Accept':'image/png,image/jpeg,image/webp,image/gif,image/bmp,image/tiff','User-Agent':'KnowledgeService-ImageImport/1','Accept-Encoding':'identity'})
        response=connection.getresponse();headers=dict(response.getheaders())
        if response.status in {301,302,303,307,308}: return response.status,headers,b''
        length=response.getheader('Content-Length')
        if length is not None and (not length.isdigit() or int(length)>max_bytes): raise RemoteImageError('Remote image exceeds byte limit')
        chunks=[];count=0
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0: raise RemoteImageError('Remote image timed out')
            if connection.sock is not None: connection.sock.settimeout(remaining)
            part=response.read1(min(65536,max_bytes+1-count))
            if not part: break
            chunks.append(part);count+=len(part)
            if count>max_bytes: raise RemoteImageError('Remote image exceeds byte limit')
        return response.status,headers,b''.join(chunks)
    finally:
        watchdog.cancel();connection.close();watchdog.join(timeout=.05)


class RemoteImageImporter:
    def __init__(self,*,resolver=None,transport=None,timeout=5,max_bytes=MAX_IMAGE_BYTES,max_pixels=MAX_IMAGE_PIXELS,max_redirects=3):
        self.resolver=resolver or _resolve;self.transport=transport or _request
        self.timeout,self.max_bytes,self.max_pixels,self.max_redirects=timeout,max_bytes,max_pixels,max_redirects

    def fetch(self,url):
        original=url;deadline=time.monotonic()+self.timeout
        try:
            for redirect in range(self.max_redirects+1):
                if len(url)>4096 or any(ord(char)<33 or ord(char)==127 for char in url): raise RemoteImageError('Unsafe image URL')
                parsed=urlsplit(url)
                port=parsed.port or (443 if parsed.scheme=='https' else 80)
                if parsed.scheme not in {'https','http'} or not parsed.hostname or parsed.username is not None or parsed.password is not None or port!=(443 if parsed.scheme=='https' else 80):
                    raise RemoteImageError('Unsafe image URL')
                host=parsed.hostname.encode('idna').decode('ascii')
                if host.lower().rstrip('.') in {'localhost','metadata.google.internal'}: raise RemoteImageError('Private image destination')
                try: addresses=[str(ipaddress.ip_address(host))]
                except ValueError: addresses=list(_bounded_resolve(self.resolver,host,port,deadline-time.monotonic()))
                if not addresses or not all(_public(address) for address in addresses): raise RemoteImageError('Private image destination')
                remaining=deadline-time.monotonic()
                if remaining<=0: raise RemoteImageError('Remote image timed out')
                status,headers,data=self.transport(url,addresses[0],remaining,self.max_bytes)
                headers={k.lower():v for k,v in headers.items()}
                if status in {301,302,303,307,308}:
                    if redirect>=self.max_redirects or not headers.get('location'): raise RemoteImageError('Remote image redirect limit')
                    url=urljoin(url,headers['location']);continue
                if status!=200: raise RemoteImageError('Remote image is unavailable')
                if headers.get('content-encoding','identity').lower()!='identity': raise RemoteImageError('Encoded image response is unsupported')
                mime=headers.get('content-type','').split(';',1)[0].strip().lower()
                details=inspect_image(data,mime,max_bytes=self.max_bytes,max_pixels=self.max_pixels)
                return dict(data=data,**details,provenance=dict(original_url=original,resolved_url=url))
        except RemoteImageError: raise
        except Exception as exc: raise RemoteImageError('Remote image import failed') from exc
