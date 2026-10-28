param(
    [string]$Path = "",
    [string]$Language = "",
    [string]$OutputPath = "",
    [switch]$ListLanguages
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

function Write-Result {
    param([hashtable]$Payload)
    $json = $Payload | ConvertTo-Json -Compress -Depth 5
    if ($OutputPath) {
        [System.IO.File]::WriteAllText($OutputPath, $json, $utf8)
    }
    else {
        Write-Output $json
    }
}

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]

    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq "AsTask" -and
            $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq "IAsyncOperation``1"
        } |
        Select-Object -First 1

    function Await-Result {
        param($Operation, [Type]$ResultType)
        $method = $asTask.MakeGenericMethod($ResultType)
        $task = $method.Invoke($null, @($Operation))
        $task.Wait()
        return $task.Result
    }

    $languages = @(
        [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages |
            ForEach-Object { $_.LanguageTag }
    )

    if ($ListLanguages) {
        Write-Result @{
            ok = $true
            languages = $languages
        }
        exit 0
    }

    if (-not $Path) {
        Write-Result @{
            ok = $false
            error = "missing_path"
            message = "No image path was provided."
            languages = $languages
        }
        exit 2
    }

    $engine = $null
    $selected = ""
    if ($Language) {
        $languageObject = New-Object Windows.Globalization.Language -ArgumentList $Language
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($languageObject)
        $selected = $Language
    }
    if ($null -eq $engine) {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
        $selected = "user-profile"
    }
    if ($null -eq $engine) {
        Write-Result @{
            ok = $false
            error = "ocr_unavailable"
            message = "Windows OCR is unavailable or no OCR language pack is installed."
            languages = $languages
        }
        exit 3
    }

    $file = Await-Result `
        ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) `
        ([Windows.Storage.StorageFile])
    $stream = Await-Result `
        ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) `
        ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder = Await-Result `
            ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) `
            ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await-Result `
            ($decoder.GetSoftwareBitmapAsync()) `
            ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            $result = Await-Result `
                ($engine.RecognizeAsync($bitmap)) `
                ([Windows.Media.Ocr.OcrResult])
            $lines = @($result.Lines | ForEach-Object { $_.Text })
            Write-Result @{
                ok = $true
                text = [string]$result.Text
                lines = $lines
                line_count = $lines.Count
                language = $selected
                engine_language = $engine.RecognizerLanguage.LanguageTag
                languages = $languages
            }
        }
        finally {
            if ($null -ne $bitmap) { $bitmap.Dispose() }
        }
    }
    finally {
        if ($null -ne $stream) { $stream.Dispose() }
    }
}
catch {
    Write-Result @{
        ok = $false
        error = $_.Exception.GetType().Name
        message = $_.Exception.Message
    }
    exit 1
}
