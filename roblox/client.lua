--[[
    roblox/client.lua
    -----------------
    Luarmor-style Roblox license key loader.

    HOW IT WORKS:
    1.  On startup the script collects a hardware fingerprint (HWID)
        derived from the local player's UserId (available in executor environments).
    2.  It prompts the user for their license key (or reads it from a saved file).
    3.  It sends the key + HWID to your Flask API via HttpService:PostAsync.
    4.  If the API returns status="valid" it verifies the response signature
        (anti-tamper) and then loads & executes the protected main script.
    5.  Any other status causes an informative error and the script exits.

    SETUP:
    •   Replace API_URL with your actual server URL.
    •   Replace API_SECRET with the same API_MASTER_TOKEN used in the backend.
    •   Replace MAIN_SCRIPT_URL (or embed your script inline) with the URL
        of the main protected Lua script.

    NOTE:
    •   HttpService must be enabled in your game (or your executor handles it).
    •   loadstring() must be enabled if using the remote script payload method.
]]

-- ──────────────────────────────────────────────────────────────────────────
-- Config  (change these before deploying)
-- ──────────────────────────────────────────────────────────────────────────
local API_URL = "https://roblox-keys-production.up.railway.app/check"
   -- Flask /check endpoint
local API_SECRET = "bfa3ba6b4cdc5584d1f60865f86087118a782500a8c987304bc03a14ad7185b1"

-- (Main script is now delivered from the server - no URL needed here)

-- ──────────────────────────────────────────────────────────────────────────
-- Services
-- ──────────────────────────────────────────────────────────────────────────
local HttpService  = game:GetService("HttpService")
local Players      = game:GetService("Players")

local LocalPlayer  = Players.LocalPlayer

-- ──────────────────────────────────────────────────────────────────────────
-- HWID generation
-- Derives a stable fingerprint from the executor environment.
-- In a real executor context you can call identifyexecutor() or use
-- getmachineinfo() if available; for Roblox HttpService-only contexts
-- we fall back to the UserId (per-Roblox-account locking).
-- ──────────────────────────────────────────────────────────────────────────
local function getHWID()
    -- Try executor-specific fingerprint first
    if identifyexecutor then
        -- Some executors expose a unique ID; we hash UserId + executor name
        return tostring(LocalPlayer.UserId) .. "_" .. identifyexecutor()
    end
    -- Fallback: Roblox UserId (per-account locking instead of per-device)
    return "rbx_" .. tostring(LocalPlayer.UserId)
end

-- ──────────────────────────────────────────────────────────────────────────
-- Simple HMAC-SHA256 verification (Lua-side)
-- We verify the `sig` field returned by the API to confirm the response
-- was not spoofed by a local proxy / memory edit.
-- The sig is: SHA256(API_SECRET + ":" + key + ":" + status)[:16]
-- ──────────────────────────────────────────────────────────────────────────
local function computeSig(key, status, hasPayload)
    -- Roblox/executors don't expose a native SHA256 — we do a best-effort
    -- lightweight polynomial hash. For production, host a signed payload instead.
    local pCheck = hasPayload and "p+" or "p-"
    local raw = API_SECRET .. ":" .. key .. ":" .. status .. ":" .. pCheck
    local h   = 5381
    for i = 1, #raw do
        h = (h * 33 + string.byte(raw, i)) % 2^32
    end
    -- Return first 16 hex chars to mirror the server format
    return string.format("%016x", h):sub(1, 16)
end

-- ──────────────────────────────────────────────────────────────────────────
local function promptForKey()
    -- NEW: Check for the 1-line loader global variable first
    if _G.script_key and #_G.script_key > 10 then
        return _G.script_key
    end

    -- Try to read from a previously saved key file (executor writefile/readfile)
    if readfile and writefile then
        local ok, saved = pcall(readfile, "license_key.txt")
        if ok and saved and #saved > 10 then
            return saved:gsub("^%s+", ""):gsub("%s+$", "")
        end
    end

    -- Fall back to a ScreenGui text-input prompt
    local screenGui = Instance.new("ScreenGui")
    screenGui.Name          = "LicensePrompt"
    screenGui.ResetOnSpawn  = false
    screenGui.ZIndexBehavior = Enum.ZIndexBehavior.Sibling
    screenGui.Parent        = game:GetService("CoreGui")

    local bg = Instance.new("Frame")
    bg.Size            = UDim2.new(0, 420, 0, 200)
    bg.AnchorPoint     = Vector2.new(0.5, 0.5)
    bg.Position        = UDim2.new(0.5, 0, 0.5, 0)
    bg.BackgroundColor3= Color3.fromRGB(20, 20, 30)
    bg.BorderSizePixel = 0
    bg.Parent          = screenGui
    Instance.new("UICorner", bg).CornerRadius = UDim.new(0, 12)

    local title = Instance.new("TextLabel")
    title.Size             = UDim2.new(1, 0, 0, 40)
    title.BackgroundTransparency = 1
    title.Text             = "🔑  Enter Your License Key"
    title.TextColor3       = Color3.fromRGB(220, 200, 255)
    title.Font             = Enum.Font.GothamBold
    title.TextSize         = 18
    title.Parent           = bg

    local box = Instance.new("TextBox")
    box.Size               = UDim2.new(0.9, 0, 0, 44)
    box.AnchorPoint        = Vector2.new(0.5, 0)
    box.Position           = UDim2.new(0.5, 0, 0, 54)
    box.BackgroundColor3   = Color3.fromRGB(35, 35, 55)
    box.BorderSizePixel    = 0
    box.PlaceholderText    = "LIC-XXXX-XXXX-XXXX-XXXX"
    box.TextColor3         = Color3.fromRGB(255, 255, 255)
    box.PlaceholderColor3  = Color3.fromRGB(100, 100, 130)
    box.Font               = Enum.Font.Code
    box.TextSize           = 16
    box.ClearTextOnFocus   = false
    box.Parent             = bg
    Instance.new("UICorner", box).CornerRadius = UDim.new(0, 8)

    local submitBtn = Instance.new("TextButton")
    submitBtn.Size           = UDim2.new(0.5, 0, 0, 40)
    submitBtn.AnchorPoint    = Vector2.new(0.5, 0)
    submitBtn.Position       = UDim2.new(0.5, 0, 0, 110)
    submitBtn.BackgroundColor3 = Color3.fromRGB(90, 60, 200)
    submitBtn.Text           = "Validate"
    submitBtn.TextColor3     = Color3.fromRGB(255, 255, 255)
    submitBtn.Font           = Enum.Font.GothamBold
    submitBtn.TextSize       = 16
    submitBtn.Parent         = bg
    Instance.new("UICorner", submitBtn).CornerRadius = UDim.new(0, 8)

    local statusLabel = Instance.new("TextLabel")
    statusLabel.Size           = UDim2.new(1, 0, 0, 30)
    statusLabel.Position       = UDim2.new(0, 0, 0, 160)
    statusLabel.BackgroundTransparency = 1
    statusLabel.Text           = ""
    statusLabel.TextColor3     = Color3.fromRGB(255, 100, 100)
    statusLabel.Font           = Enum.Font.Gotham
    statusLabel.TextSize       = 14
    statusLabel.Parent         = bg

    -- Wait for submit
    local resultKey = nil
    local conn
    conn = submitBtn.MouseButton1Click:Connect(function()
        local k = box.Text:gsub("^%s+", ""):gsub("%s+$", "")
        if #k < 10 then
            statusLabel.Text = "⚠️  Key too short. Check your input."
            return
        end
        resultKey = k
        conn:Disconnect()
    end)

    while resultKey == nil do task.wait(0.05) end
    screenGui:Destroy()

    -- Save for next session
    if writefile then pcall(writefile, "license_key.txt", resultKey) end
    return resultKey
end

-- ──────────────────────────────────────────────────────────────────────────
-- Main validation flow
-- ──────────────────────────────────────────────────────────────────────────
local function runMainScript(payload)
    if payload then
        local fn, err = loadstring(payload)
        if not fn then
            error("[License] Failed to parse script payload: " .. tostring(err))
            return
        end
        fn()
    else
        error("[License] Server did not provide a script payload.")
    end
end

local function validate()
    -- 1. Collect inputs
    local hwid = getHWID()
    local key  = promptForKey()

    -- 2. Build POST body
    local payload = HttpService:JSONEncode({ key = key, hwid = hwid })

    -- 3. Send request
    local ok, rawResponse = pcall(
        HttpService.PostAsync,
        HttpService,
        API_URL,
        payload,
        Enum.HttpContentType.ApplicationJson,
        false
    )

    if not ok then
        warn("[License] HTTP request failed: " .. tostring(rawResponse))
        if rawResponse and rawResponse:find("403") then
            error("[License] Key invalid or server rejected the request.")
        end
        error("[License] Could not reach the license server. Try again later.")
        return
    end

    -- 4. Parse response JSON
    local ok2, data = pcall(HttpService.JSONDecode, HttpService, rawResponse)
    if not ok2 or type(data) ~= "table" then
        error("[License] Malformed server response. Contact support.")
        return
    end

    -- 5. Anti-tamper: verify signature
    local hasPayload = (data.payload ~= nil)
    local expectedSig = computeSig(key, data.status or "", hasPayload)
    if data.sig ~= expectedSig then
        -- Signature mismatch: could be a proxy spoofing a valid response
        error("[License] ⚠️ Response integrity check failed. Do not bypass the license system.")
        return
    end

    -- 6. Act on status
    local status = data.status
    if status == "valid" then
        print("[License] ✅ Key validated! Loading script …")
        runMainScript(data.payload)
    elseif status == "expired" then
        error("[License] ❌ Your license key has expired. Purchase a new one.")
    elseif status == "revoked" then
        error("[License] ❌ Your license key has been revoked.")
    elseif status == "hwid_mismatch" then
        error("[License] ❌ HWID mismatch. This key is locked to another device.")
    else
        error("[License] ❌ Invalid key. Please check your key and try again.")
    end
end

-- ──────────────────────────────────────────────────────────────────────────
-- Entry point  (wrapped in a protected call to show a clean error message)
-- ──────────────────────────────────────────────────────────────────────────
local success, err = pcall(validate)
if not success then
    -- Surface the error visibly in the Roblox chat / output
    warn(err)
    -- Optionally kick the player so the game doesn't run without a valid key
    -- LocalPlayer:Kick(tostring(err))
end
