# 项目长期记忆

## Windows 终端编码

- PowerShell 命令默认设置 UTF-8，避免中文源码、日志和命令参数乱码：

```powershell
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
```

## Windows 下使用 `apply_patch`

本机的 `apply_patch` 是一个批处理包装器：

```text
apply_patch.bat -> codex.exe --codex-run-as-apply-patch %*
```

补丁工具要求完整补丁作为一个 UTF-8 命令行参数传入，不从标准输入读取。因此：

- 不要使用 `patch | apply_patch`，否则可能报 `requires a UTF-8 PATCH argument`。
- 中文多行补丁经过 PowerShell here-string、CRLF 和 `.bat` 的 `%*` 转发时可能被破坏，表现为 `The last line of the patch must be '*** End Patch'`。
- 遇到上述问题时，不要反复修改补丁内容；应统一换行为 LF，并绕过 `.bat`，通过 `.NET ProcessStartInfo.ArgumentList` 直接调用底层 `codex.exe`。

已验证可用的 PowerShell 模板：

```powershell
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()

$patch = @'
*** Begin Patch
*** Update File: path/to/file
@@
-old text
+new text
*** End Patch
'@ -replace "`r`n", "`n"

$wrapper = (Get-Command apply_patch).Source
$wrapperCommand = Get-Content -LiteralPath $wrapper | Select-Object -Skip 1 -First 1
if ($wrapperCommand -notmatch '^"([^"]+)"') {
    throw "无法从 apply_patch 包装器解析 codex.exe 路径"
}
$codexExe = $Matches[1]

$processInfo = [System.Diagnostics.ProcessStartInfo]::new()
$processInfo.FileName = $codexExe
$processInfo.UseShellExecute = $false
$processInfo.RedirectStandardOutput = $true
$processInfo.RedirectStandardError = $true
[void]$processInfo.ArgumentList.Add('--codex-run-as-apply-patch')
[void]$processInfo.ArgumentList.Add($patch.TrimEnd("`n"))

$process = [System.Diagnostics.Process]::Start($processInfo)
$stdout = $process.StandardOutput.ReadToEnd()
$stderr = $process.StandardError.ReadToEnd()
$process.WaitForExit()

Write-Output $stdout
if ($stderr) {
    Write-Error $stderr
}
if ($process.ExitCode -ne 0) {
    throw "apply_patch failed with exit code $($process.ExitCode)"
}
```

成功时必须看到类似输出：

```text
Success. Updated the following files:
M path/to/file
```

如果命令被安全策略直接拒绝，说明补丁没有执行；不要把策略拒绝误判为项目或补丁语法错误。
