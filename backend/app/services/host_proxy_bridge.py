"""Forward approved Docker traffic to a host-local Clash HTTP proxy."""
import ipaddress
import os
import select
import socket
import threading


def _client_is_allowed(
    address: str,
    allowed_network: ipaddress.IPv4Network | ipaddress.IPv6Network,
) -> bool:
    """Check whether a connecting peer address belongs to the approved Docker CIDR.

    Args:
        address: Numeric peer IP address supplied by the accepted TCP socket.
        allowed_network: IPv4/IPv6 network permitted to use the host proxy bridge.

    Returns:
        True when the peer IP belongs to the allowlisted network; invalid addresses
        are rejected without opening an upstream Clash connection.
    """
    try:
        return ipaddress.ip_address(address) in allowed_network
    except ValueError:
        return False


def _relay_connection(
    client: socket.socket,
    upstream_host: str,
    upstream_port: int,
    allowed_network: ipaddress.IPv4Network | ipaddress.IPv6Network,
) -> None:
    """Relay one allowlisted TCP stream to the host-local Clash listener.

    Args:
        client: Accepted Docker-side TCP socket; peer address is checked first.
        upstream_host: Hostname or IP of the local proxy, usually loopback.
        upstream_port: Clash mixed-port in the range 1 through 65535.
        allowed_network: Client subnet authorized to reach the proxy bridge.

    Returns:
        None; the two TCP streams are closed when both sides finish or the idle
        timeout expires. Proxy payloads are passed unchanged and never logged.
    """
    upstream: socket.socket | None = None
    try:
        peer_address = client.getpeername()[0]
        if not _client_is_allowed(peer_address, allowed_network):
            return
        upstream = socket.create_connection((upstream_host, upstream_port), timeout=5)
        client.setblocking(True)
        upstream.setblocking(True)
        active = [client, upstream]
        while active:
            readable, _, exceptional = select.select(active, [], active, 900)
            if exceptional or not readable:
                break
            for source in readable:
                payload = source.recv(65536)
                destination = upstream if source is client else client
                if payload:
                    destination.sendall(payload)
                    continue
                active.remove(source)
                try:
                    destination.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
    except OSError:
        # Keep request bytes and proxy response details out of service logs.
        pass
    finally:
        client.close()
        if upstream is not None:
            upstream.close()


def serve() -> None:
    """Serve a subnet-restricted bridge on the Docker host gateway interface.

    Environment variables configure the bind IPv4 address/port, loopback Clash
    host/port, and allowlisted Docker CIDR. The process handles each client in a
    daemon thread and never changes the host Clash configuration.

    Returns:
        None; the function runs until the container receives a termination signal.
    """
    bind_host = os.environ.get('PROXY_BRIDGE_BIND_ADDRESS', '172.17.0.1')
    bind_port = int(os.environ.get('PROXY_BRIDGE_BIND_PORT', '17897'))
    upstream_host = os.environ.get('PROXY_BRIDGE_UPSTREAM_HOST', '127.0.0.1')
    upstream_port = int(os.environ.get('PROXY_BRIDGE_UPSTREAM_PORT', '7897'))
    allowed_network = ipaddress.ip_network(
        os.environ.get('PROXY_BRIDGE_CLIENT_CIDR', '172.26.0.0/16'),
        strict=False,
    )
    ipaddress.ip_address(bind_host)
    if not 1 <= bind_port <= 65535 or not 1 <= upstream_port <= 65535:
        raise ValueError('proxy bridge ports must be in the range 1..65535')

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((bind_host, bind_port))
        listener.listen(64)
        while True:
            client, _ = listener.accept()
            thread = threading.Thread(
                target=_relay_connection,
                args=(client, upstream_host, upstream_port, allowed_network),
                daemon=True,
            )
            thread.start()


if __name__ == '__main__':
    serve()
