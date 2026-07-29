# LAN IPv4 of Windows (skip hotspot/virtual adapters).
$ips = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object {
        $_.IPAddress -match '^192\.168\.\d+\.\d+$' -and
        $_.IPAddress -ne '192.168.56.1' -and
        $_.IPAddress -notlike '192.168.137.*' -and
        $_.InterfaceAlias -notmatch 'vEthernet|VirtualBox|VMware|Loopback|TAP|TUN|Tailscale|ZeroTier|Radmin'
    } |
    Sort-Object @{
        Expression = {
            if ($_.InterfaceAlias -match 'Wi-Fi|WLAN|Wireless|Ethernet|LAN') { 0 } else { 1 }
        }
    }, @{ Expression = { $_.IPAddress } }

if ($ips) {
    $ips[0].IPAddress
}
