param([string]$Runtime = (Join-Path $env:USERPROFILE 'Apps\VallpaperTTS'))
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$voiceFolder = Join-Path $Runtime 'voices'
New-Item -ItemType Directory -Force $voiceFolder | Out-Null
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $installed = @($synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name })
    $pairs = @(@('Windows-neutral', 'Microsoft David Desktop'), @('Windows-Zira', 'Microsoft Zira Desktop'))
    foreach ($pair in $pairs) {
        if ($installed -notcontains $pair[1]) {
            Write-Warning "$($pair[1]) is unavailable. Install the English Windows voice or supply your own reference."
            continue
        }
        $synth.SelectVoice($pair[1])
        $path = Join-Path $voiceFolder ($pair[0] + '.wav')
        $synth.SetOutputToWaveFile($path)
        $synth.Speak('The desert stretched beyond the horizon. I stood quietly and listened to the wind. Every journey begins with a single step, and every voice has a story to tell.')
        $synth.SetOutputToNull()
        Write-Output "Created synthetic reference: $path"
    }
} finally {
    $synth.Dispose()
}
