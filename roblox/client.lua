--[[
    roblox/client.lua
    -----------------
    Luarmor-style Roblox license key loader.
    VERSION 1.2 (Ultra Debug)
]]

-- 🚨 THE MOST IMPORTANT LINE: Confirmation of start
print("--------------------------------------------------")
print("[LICENSE SYSTEM] BOOTING UP...")
print("--------------------------------------------------")

-- ──────────────────────────────────────────────────────────────────────────
-- Config
-- ──────────────────────────────────────────────────────────────────────────
local API_URL = "https://roblox-keys-production.up.railway.app/check"
local API_SECRET = "bfa3ba6b4cdc5584d1f60865f86087118a782500a8c987304bc03a14ad7185b1"

-- ──────────────────────────────────────────────────────────────────────────
-- Services
-- ──────────────────────────────────────────────────────────────────────────
local HttpService  = game:GetService("HttpService")
local Players      = game:GetService("Players")
local StarterGui   = game:GetService("StarterGui")
local LocalPlayer  = Players.LocalPlayer

-- ──────────────────────────────────────────────────────────────────────────
-- Notifications
-- ──────────────────────────────────────────────────────────────────────────
local function notify(title, text, duration)
    pcall(function()
        StarterGui:SetCore("SendNotification", {
            Title = title or "License System",
            Text  = text  or "Working...",
            Duration = duration or 5
        })
    end)
end

notify("Loader", "Initializing... Please wait.", 3)

-- ──────────────────────────────────────────────────────────────────────────
-- Helpers
-- ──────────────────────────────────────────────────────────────────────────
local function getHWID()
    if identifyexecutor then
        local id = identifyexecutor()
        return tostring(LocalPlayer.UserId) .. "_" .. tostring(id)
    end
    return "rbx_" .. tostring(LocalPlayer.UserId)
end

local function computeSig(key, status, hasPayload)
    local pCheck = hasPayload and "p+" or "p-"
    local raw = API_SECRET .. ":" .. key .. ":" .. status .. ":" .. pCheck
    local h   = 5381
    for i = 1, #raw do
        h = (h * 33 + string.byte(raw, i)) % 2^32
    end
    return string.format("%08x", h)
end

local function promptForKey()
    print("[License] 🔎 Checking environment for script_key...")
    
    local autoKey = nil
    
    -- Try getgenv
    if getgenv then
        autoKey = getgenv().script_key
    end
    
    -- Try _G
    if not autoKey then
        autoKey = _G.script_key
    end
    
    -- Try getfenv
    if not autoKey then
        pcall(function()
            local env = getfenv(0)
            if env and env.script_key then
                autoKey = env.script_key
            end
        end)
    end

    if autoKey and type(autoKey) == "string" and #autoKey > 10 then
        print("[License] ✅ Key found: " .. tostring(autoKey):sub(1,10) .. "...")
        notify("Key Detected", "Automatically using script_key.", 2)
        return autoKey
    end

    -- Try saved file
    if readfile then
        local ok, saved = pcall(readfile, "license_key.txt")
        if ok and saved and #saved > 10 then
            print("[License] ✅ Loaded key from file.")
            notify("File Loaded", "Using license_key.txt.", 2)
            return saved:gsub("^%s+", ""):gsub("%s+$", "")
        end
    end

    -- Fallback to UI
    print("[License] ⚠️ Opening UI prompt...")
    notify("Input Required", "Please enter your license key.", 5)
    
    -- (UI code remains mostly same but with added debug)
    local screenGui = Instance.new("ScreenGui")
    screenGui.Name = "LicensePrompt"
    screenGui.ResetOnSpawn = false
    screenGui.Parent = game:GetService("CoreGui")

    local bg = Instance.new("Frame")
    bg.Size = UDim2.new(0, 420, 0, 220)
    bg.AnchorPoint = Vector2.new(0.5, 0.5)
    bg.Position = UDim2.new(0.5, 0, 0.5, 0)
    bg.BackgroundColor3 = Color3.fromRGB(25, 25, 35)
    bg.Parent = screenGui
    Instance.new("UICorner", bg).CornerRadius = UDim.new(0, 15)

    local title = Instance.new("TextLabel")
    title.Size = UDim2.new(1, 0, 0, 60)
    title.BackgroundTransparency = 1
    title.Text = "Enter License Key"
    title.TextColor3 = Color3.fromRGB(255, 255, 255)
    title.Font = Enum.Font.GothamBold
    title.TextSize = 22
    title.Parent = bg

    local box = Instance.new("TextBox")
    box.Size = UDim2.new(0.85, 0, 0, 50)
    box.AnchorPoint = Vector2.new(0.5, 0)
    box.Position = UDim2.new(0.5, 0, 0, 70)
    box.BackgroundColor3 = Color3.fromRGB(40, 40, 55)
    box.PlaceholderText = "LIC-XXXX-XXXX-XXXX-XXXX"
    box.TextColor3 = Color3.fromRGB(255, 255, 255)
    box.Parent = bg
    Instance.new("UICorner", box).CornerRadius = UDim.new(0, 10)

    local submitBtn = Instance.new("TextButton")
    submitBtn.Size = UDim2.new(0.5, 0, 0, 45)
    submitBtn.AnchorPoint = Vector2.new(0.5, 0)
    submitBtn.Position = UDim2.new(0.5, 0, 0, 135)
    submitBtn.BackgroundColor3 = Color3.fromRGB(80, 50, 200)
    submitBtn.Text = "Login"
    submitBtn.TextColor3 = Color3.fromRGB(255, 255, 255)
    submitBtn.Font = Enum.Font.GothamBold
    submitBtn.TextSize = 18
    submitBtn.Parent = bg
    Instance.new("UICorner", submitBtn).CornerRadius = UDim.new(0, 12)

    local res = nil
    submitBtn.MouseButton1Click:Connect(function()
        local t = box.Text:gsub("%s+", "")
        if #t > 15 then
            res = t
        end
    end)

    while res == nil do task.wait(0.1) end
    screenGui:Destroy()
    
    if writefile then pcall(writefile, "license_key.txt", res) end
    return res
end

local function validate()
    print("[License] 🛰️ Contacting server...")
    local hwid = getHWID()
    local key  = promptForKey()

    local body = HttpService:JSONEncode({ key = key, hwid = hwid })
    print("[License] ⏳ Validating...")
    
    local responseBody
    local ok, err = pcall(function()
        -- Attempt to use executor's native request if available (more stable)
        local req = (syn and syn.request) or (http and http.request) or http_request or (Fluxus and Fluxus.request) or request
        if req then
            local res = req({
                Url = API_URL,
                Method = "POST",
                Headers = { ["Content-Type"] = "application/json" },
                Body = body
            })
            responseBody = res.Body
        else
            -- Fallback to standard HttpService
            responseBody = HttpService:PostAsync(API_URL, body, Enum.HttpContentType.ApplicationJson)
        end
    end)

    if not ok or not responseBody then
        notify("Error", "Server connection failed.", 5)
        error("[License] ❌ Connection Error: " .. tostring(err or "Response Empty"))
    end

    local data = HttpService:JSONDecode(responseBody)
    print("[License] 📡 Response received: " .. tostring(data.status))

    local sig = computeSig(key, data.status or "", data.payload ~= nil)
    if data.sig ~= sig then
        notify("Security Alert", "Response integrity check failed.", 5)
        error("[License] 🛡️ Tamper detected!")
    end

    if data.status == "valid" then
        print("[License] ✅ SUCCESS!")
        notify("Success", "Welcome! Loading script...", 5)
        
        if data.payload then
            local f, err = loadstring(data.payload)
            if f then
                print("[License] 🚀 EXECUTING MAIN SCRIPT")
                f()
            else
                error("[License] ❌ Script Load Error: " .. tostring(err))
            end
        else
            error("[License] ❌ No payload script returned.")
        end
    else
        notify("Denied", "Reason: " .. tostring(data.status), 5)
        error("[License] ❌ Access Denied: " .. tostring(data.status))
    end
end

-- MAIN START
local s, e = pcall(validate)
if not s then
    warn(tostring(e))
    print("[License] 🛑 LOAD ABORTED.")
end
