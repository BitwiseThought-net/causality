$Error.Clear() 
$global:needsReboot = $false 

Function Check-Action { 
    param ([string]$Name, [scriptblock]$Check, [scriptblock]$Action) 
    Write-Host "Checking $Name..." -NoNewline 
    if (Invoke-Command -ScriptBlock $Check -ErrorAction SilentlyContinue) { 
        Write-Host " [ALREADY DONE]" -ForegroundColor Green 
        return $false 
    } else { 
        Write-Host " [MISSING]" -ForegroundColor Yellow 
        Invoke-Command -ScriptBlock $Action 
        return $true 
    } 
} 

# 1. WSL Features 
$null = Check-Action "WSL Features" `
    -Check { (Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux).State -eq "Enabled" } `
    -Action { 
        dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart 
        dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart 
        $global:needsReboot = $true 
    } 

# 2. Ubuntu Distribution 
$null = Check-Action "Ubuntu Distro" `
    -Check { $list = wsl --list --quiet 2>$null | ForEach-Object { $_.Replace("`0", "") }; $list -like "*Ubuntu*" } `
    -Action { 
        Write-Host "Downloading Ubuntu (this may take a minute)..." -ForegroundColor Cyan 
        wsl --install -d Ubuntu --no-launch 
        $global:needsReboot = $true 
    } 

# 3. Core Tools
$null = Check-Action "VS Code" `
    -Check { Get-Command code -ErrorAction SilentlyContinue } `
    -Action { winget install -e --id Microsoft.VisualStudioCode --accept-package-agreements } 

$null = Check-Action "Docker Desktop" `
    -Check { (Get-Process "Docker Desktop" -ErrorAction SilentlyContinue) -or (Test-Path "$env:ProgramFiles\Docker\Docker") } `
    -Action { winget install -e --id Docker.DockerDesktop --accept-package-agreements; $global:needsReboot = $true } 

$null = Check-Action "Git" `
    -Check { Get-Command git -ErrorAction SilentlyContinue } `
    -Action { winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements } 

# UPDATED: Using Registry check for WinMerge for better reliability
$null = Check-Action "WinMerge" `
    -Check { 
        (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like "*WinMerge*" }) -or 
        (Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like "*WinMerge*" })
    } `
    -Action { 
        winget install -e --id WinMerge.WinMerge --accept-package-agreements --accept-source-agreements 
        $Error.Clear()
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User") 
    } 

# 4. Refresh Path and Extensions 
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User") 

$extensions = @("ms-azuretools.vscode-docker", "ms-vscode-remote.remote-containers", "ms-vscode-remote.remote-wsl") 
$installedExts = cmd /c "code --list-extensions" 
foreach ($ext in $extensions) { 
    if ($installedExts -notcontains $ext) { 
        Write-Host "Installing extension: $ext" -ForegroundColor Yellow 
        cmd /c "code --install-extension $ext --force" 
    } 
} 

# 5. Verify Ubuntu-Docker Integration
$null = Check-Action "Docker-Ubuntu Integration" `
    -Check { $status = wsl -d Ubuntu docker version 2>$null; $status -match "Engine" } `
    -Action { Write-Host " > Integration not yet active." -ForegroundColor Gray } 

# --- Final Handling & Pause --- 
$realErrors = $Error | Where-Object { $_.Exception -notmatch "WSL" -and $_.Exception -notmatch "winget" -and $_.Exception -notmatch "code" } 

if ($realErrors.Count -gt 0 -and !$global:needsReboot) { 
    Write-Host "`n[!] Some errors occurred. Please review the output above." -ForegroundColor Red 
} elseif ($global:needsReboot) { 
    Write-Host "`n[!] SETUP STEPS COMPLETED. REBOOT REQUIRED." -ForegroundColor Yellow 
    Write-Host "A reboot will start in 30 seconds. PRESS ANY KEY TO CANCEL." -ForegroundColor Cyan 
    for ($i = 30; $i -gt 0; $i--) { 
        Write-Progress -Activity "Rebooting in $i seconds" -Status "Press any key to stop" -PercentComplete ($i/30*100) 
        Start-Sleep -Seconds 1 
        if ($Host.UI.RawUI.KeyAvailable) { 
            Write-Host "`n[X] Reboot cancelled. Please reboot manually when ready." -ForegroundColor Magenta 
            break 
        } 
        if ($i -eq 1) { Restart-Computer -Force } 
    } 
} else { 
    Write-Host "`n[SUCCESS] Everything is already set up!" -ForegroundColor Green 
    
    $integrationIsWorking = wsl -d Ubuntu docker version 2>$null | Select-String "Engine" 
    $dockerPath = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe" 

    if (!$integrationIsWorking) { 
        Write-Host "`nMANUAL CONFIGURATION NEEDED:" -ForegroundColor Yellow 
        Write-Host " 1. Accept the Docker Service Agreement (if prompted)." 
        Write-Host " 2. Go to Settings > Resources > WSL Integration." 
        Write-Host " 3. Toggle 'Ubuntu' to ON and click 'Apply & Restart'." 
        Write-Host "`nPress any key to launch Docker Desktop and follow these steps..." -ForegroundColor Cyan 
        $null = $host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") 
        if (Test-Path $dockerPath) { Start-Process $dockerPath } 
    } else { 
        Write-Host "`nVERIFIED: Ubuntu and Docker are communicating perfectly." -ForegroundColor Green 
        Write-Host "Press any key to launch VS Code and start coding..." -ForegroundColor Cyan 
        $null = $host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown") 
        code . 
    } 
} 

Write-Host "`nScript finished. Press any key to exit..." -ForegroundColor Yellow 
$null = $host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
