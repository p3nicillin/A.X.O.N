$ErrorActionPreference = 'Stop'

function Write-Result($value) {
    $value | ConvertTo-Json -Depth 8 -Compress
}

try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $desktop = [System.Windows.Automation.AutomationElement]::RootElement

    function Runtime-Id($element) {
        try { return (($element.GetRuntimeId() | ForEach-Object { [string]$_ }) -join '.') }
        catch { return '' }
    }

    function Top-Level($element) {
        if ($null -eq $element) { return $null }
        $current = $element
        for ($i = 0; $i -lt 64; $i++) {
            $parent = $walker.GetParent($current)
            if ($null -eq $parent -or $parent.Equals($desktop)) { return $current }
            $current = $parent
        }
        return $current
    }

    function Children($element) {
        $items = [System.Collections.Generic.List[object]]::new()
        $child = $walker.GetFirstChild($element)
        while ($null -ne $child) {
            $items.Add($child)
            $child = $walker.GetNextSibling($child)
        }
        return $items
    }

    function Controls($root, [int]$limit = 120) {
        $found = [System.Collections.Generic.List[object]]::new()
        $pending = [System.Collections.Generic.Queue[object]]::new()
        foreach ($child in (Children $root)) { $pending.Enqueue($child) }
        while ($pending.Count -gt 0 -and $found.Count -lt $limit) {
            $item = $pending.Dequeue()
            try {
                if (-not $item.Current.IsOffscreen) { $found.Add($item) }
                if ($pending.Count -lt ($limit * 3)) {
                    foreach ($child in (Children $item)) { $pending.Enqueue($child) }
                }
            } catch { }
        }
        return $found
    }

    function Safe-Name($element) {
        try {
            if ($element.Current.IsPassword) { return '<protected>' }
            $name = [string]$element.Current.Name
            if ($name.Length -gt 160) { return $name.Substring(0, 160) }
            return $name
        } catch { return '' }
    }

    function Describe($element, [int]$index) {
        try {
            $rect = $element.Current.BoundingRectangle
            $role = [string]$element.Current.ControlType.ProgrammaticName
            $role = $role.Replace('ControlType.', '')
            $patternObject = $null
            $canFill = $element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$patternObject)
            $canInvoke = $element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$patternObject)
            $canSelect = $element.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$patternObject)
            $canToggle = $element.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$patternObject)
            $canExpand = $element.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$patternObject)
            return [ordered]@{
                id = "u$index"
                runtime_id = Runtime-Id $element
                role = $role
                label = Safe-Name $element
                enabled = [bool]$element.Current.IsEnabled
                protected = [bool]$element.Current.IsPassword
                can_fill = [bool]$canFill
                can_click = [bool]($canInvoke -or $canSelect -or $canToggle -or $canExpand)
                bounds = [ordered]@{
                    x = [int][Math]::Round($rect.X)
                    y = [int][Math]::Round($rect.Y)
                    width = [int][Math]::Round($rect.Width)
                    height = [int][Math]::Round($rect.Height)
                }
            }
        } catch { return $null }
    }

    function Fingerprint($root) {
        $parts = [System.Collections.Generic.List[string]]::new()
        foreach ($item in (Controls $root 80)) {
            try {
                $parts.Add("$(Runtime-Id $item)|$($item.Current.ControlType.ProgrammaticName)|$(Safe-Name $item)|$($item.Current.IsEnabled)")
            } catch { }
        }
        $bytes = [Text.Encoding]::UTF8.GetBytes(($parts -join "`n"))
        $hash = [Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
        return ([BitConverter]::ToString($hash).Replace('-', '').Substring(0, 16).ToLowerInvariant())
    }

    function Find-Control($root, [string]$runtimeId) {
        foreach ($item in (Controls $root 160)) {
            if ((Runtime-Id $item) -eq $runtimeId) { return $item }
        }
        return $null
    }

    $root = Top-Level ([System.Windows.Automation.AutomationElement]::FocusedElement)
    if ($null -eq $root) { throw 'No focused accessibility window was found.' }
    $rootId = Runtime-Id $root

    if ($request.action -eq 'inspect') {
        $elements = [System.Collections.Generic.List[object]]::new()
        $index = 1
        foreach ($item in (Controls $root 120)) {
            $record = Describe $item $index
            if ($null -ne $record -and $record.runtime_id) {
                $elements.Add($record)
                $index++
            }
        }
        Write-Result ([ordered]@{
            ok = $true
            backend = 'accessibility'
            window = Safe-Name $root
            root_id = $rootId
            count = $elements.Count
            elements = $elements
            state = Fingerprint $root
        })
        exit 0
    }

    if ([string]$request.root_id -ne $rootId) {
        throw 'The active application changed; inspect it again.'
    }
    $target = Find-Control $root ([string]$request.target_id)
    if ($null -eq $target) { throw 'That accessibility control is no longer available.' }
    if (-not $target.Current.IsEnabled -or $target.Current.IsOffscreen) {
        throw 'That accessibility control is unavailable or disabled.'
    }
    $before = Fingerprint $root

    if ($request.action -eq 'fill') {
        if ($target.Current.IsPassword) { throw 'Protected credential fields are not automated.' }
        $patternObject = $null
        if ($target.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$patternObject)) {
            $pattern = [System.Windows.Automation.ValuePattern]$patternObject
            if ($pattern.Current.IsReadOnly) { throw 'That accessibility control is read-only.' }
            $pattern.SetValue([string]$request.text)
            $verified = $pattern.Current.Value -eq [string]$request.text
        } else {
            throw 'That accessibility control does not support text input.'
        }
        Write-Result ([ordered]@{
            ok = $verified
            backend = 'accessibility'
            element_id = [string]$request.element_id
            characters = ([string]$request.text).Length
            verification = [ordered]@{
                verified = $verified
                reason = $(if ($verified) { 'control value matches requested text' } else { 'control value did not match' })
            }
        })
        exit $(if ($verified) { 0 } else { 1 })
    }

    if ($request.action -eq 'click') {
        $patternObject = $null
        $acted = $false
        if ($target.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$patternObject)) {
            ([System.Windows.Automation.InvokePattern]$patternObject).Invoke(); $acted = $true
        } elseif ($target.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$patternObject)) {
            ([System.Windows.Automation.SelectionItemPattern]$patternObject).Select(); $acted = $true
        } elseif ($target.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$patternObject)) {
            ([System.Windows.Automation.TogglePattern]$patternObject).Toggle(); $acted = $true
        } elseif ($target.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$patternObject)) {
            ([System.Windows.Automation.ExpandCollapsePattern]$patternObject).Expand(); $acted = $true
        }
        if (-not $acted) { throw 'That accessibility control has no safe action pattern.' }
        Start-Sleep -Milliseconds 350
        $currentRoot = Top-Level ([System.Windows.Automation.AutomationElement]::FocusedElement)
        if ($null -eq $currentRoot) { $currentRoot = $root }
        $after = Fingerprint $currentRoot
        $changed = $before -ne $after -or (Runtime-Id $currentRoot) -ne $rootId
        $expected = ([string]$request.expected).ToLowerInvariant()
        $expectedMet = -not $expected
        if ($expected) {
            foreach ($item in (Controls $currentRoot 120)) {
                if ((Safe-Name $item).ToLowerInvariant().Contains($expected)) { $expectedMet = $true; break }
            }
        }
        $verified = $changed -and $expectedMet
        $reason = $(if (-not $expectedMet) { 'expected outcome was not found' } elseif ($changed) { 'application accessibility state changed' } else { 'no observable application change' })
        Write-Result ([ordered]@{
            ok = $verified
            backend = 'accessibility'
            element_id = [string]$request.element_id
            verification = [ordered]@{
                verified = $verified
                reason = $reason
                expected_met = $expectedMet
                before = $before
                after = $after
            }
        })
        exit $(if ($verified) { 0 } else { 1 })
    }

    throw 'Unsupported accessibility action.'
} catch {
    Write-Result ([ordered]@{ ok = $false; error = $_.Exception.Message; backend = 'accessibility' })
    exit 1
}
