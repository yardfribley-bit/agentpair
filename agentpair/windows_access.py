"""Windows unattended access bootstrap; public keys only, no passwords in scripts."""
import re


def bootstrap_script(public_key):
    key = public_key.strip()
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [A-Za-z0-9@._-]+)?', key):
        raise ValueError('Expected one Ed25519 public key')
    return r'''$ErrorActionPreference='Stop'
New-Item -ItemType Directory -Force C:\AgentPairTrial | Out-Null
try {
  'installing_ssh' | Set-Content C:\AgentPairTrial\access-status.txt
  $cap=Get-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
  if($cap.State -ne 'Installed') { Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null }
  $p='C:\ProgramData\ssh\administrators_authorized_keys'
  New-Item -ItemType Directory -Force (Split-Path $p) | Out-Null
  Set-Content -Encoding ascii -Path $p -Value '__PUBLIC_KEY__'
  icacls $p /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null
  if($LASTEXITCODE -ne 0) {throw 'Authorized key ACL setup failed'}
  Set-Service sshd -StartupType Automatic
  Start-Service sshd
  Get-NetFirewallRule -Name OpenSSH-Server-In-TCP -ErrorAction Stop | Enable-NetFirewallRule
  if(-not (Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue)) {throw 'SSH listener missing'}
  'ssh_listening_authentication_pending' | Set-Content C:\AgentPairTrial\access-status.txt
} catch {
  'bootstrap_failed' | Set-Content C:\AgentPairTrial\access-status.txt
  throw
}
'''.replace('__PUBLIC_KEY__', key)
