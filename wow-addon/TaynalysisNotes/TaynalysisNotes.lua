-- Taynalysis Notes
-- Northern Sky Raid Tools keeps the raid note it receives in NSRT.StoredSharedReminder, but WoW only writes addon data to disk
-- on /reload or logout. The chat log (Logs/WoWChatLog.txt) is written as it happens, so this addon turns chat logging on and,
-- whenever a boss's part of the note changes, whispers that part to you (hidden from your chat window). The Taynalysis
-- uploader reads it from the chat log and keeps it with the time it arrived, so each pull can be scored with the note that
-- was up when it started. Every boss and every line: which assignments matter depends on the fight.
-- Each line: TAYNOTE <encounter> <note id> <n>/<count> <times>~<who>~<text>; the note id is when it was seen (Unix time),
-- <times> every time that reminder is set for (comma-separated, "0" when it has none).

local TAG = "TAYNOTE"
local MAXLEN = 240                   -- WoW drops chat messages over 255 characters
local last, queue, sending = {}, {}, false

-- the note split by boss (EncounterID header lines): encounter id -> its lines, one per reminder text and who it's for,
-- with all the times it's set for. A long list of names is split over several lines with the same times and text.
local function sections(note)
  local out, order, cur = {}, {}, nil
  for line in note:gmatch("[^\n]+") do
    local e = line:match("EncounterID:(%d+)")
    if e then
      cur = e
      if not out[e] then out[e] = {rows = {}, idx = {}}; order[#order + 1] = e end
    elseif cur then
      local t, who, text = line:match("time:([%d%.%-]+)"), line:match("tag:([^;]+)"), line:match("text:([^;]+)")
      if who and text then
        who, text = who:gsub("~", " "), text:gsub("~", " ")
        local s, key = out[cur], who .. "~" .. text
        local r = s.idx[key]
        if not r then r = {who = who, text = text, times = {}}; s.idx[key] = r; s.rows[#s.rows + 1] = r end
        if t then r.times[#r.times + 1] = t end
      end
    end
  end
  local lines = {}
  for _, e in ipairs(order) do
    local L = {}
    for _, r in ipairs(out[e].rows) do
      local times = #r.times > 0 and table.concat(r.times, ",") or "0"
      local head = (TAG .. " " .. e .. " 0000000000 000/000 "):len() + times:len() + r.text:len() + 2
      local chunk = ""
      for name in r.who:gmatch("%S+") do
        if chunk ~= "" and head + chunk:len() + 1 + name:len() > MAXLEN then
          L[#L + 1] = times .. "~" .. chunk .. "~" .. r.text; chunk = ""
        end
        chunk = chunk == "" and name or (chunk .. " " .. name)
      end
      L[#L + 1] = (times .. "~" .. chunk .. "~" .. r.text):sub(1, MAXLEN)
    end
    lines[e] = L
  end
  return lines, order
end

local function locked()
  return C_ChatInfo and C_ChatInfo.InChatMessagingLockdown and C_ChatInfo.InChatMessagingLockdown()
end

-- whisper one boss's lines to yourself, one every half second (WoW limits how fast chat can be sent), then the next boss
local function sendNext()
  local job = table.remove(queue, 1)
  if not job then sending = false; return end
  sending = true
  local id, me = time(), UnitName("player")
  for i, s in ipairs(job.lines) do
    C_Timer.After((i - 1) * 0.5, function()
      SendChatMessage((TAG .. " %s %d %d/%d %s"):format(job.enc, id, i, #job.lines, s):sub(1, 255), "WHISPER", nil, me)
      if i == #job.lines then last[job.enc] = job.key; C_Timer.After(0.5, sendNext) end
    end)
  end
end

local function check(force)
  if sending or locked() or not NSRT or type(NSRT.StoredSharedReminder) ~= "string" then return end
  local lines, order = sections(NSRT.StoredSharedReminder)
  for _, enc in ipairs(order) do
    local L = lines[enc]
    local key = table.concat(L, "\n")
    if #L > 0 and (key ~= last[enc] or force) then queue[#queue + 1] = {enc = enc, lines = L, key = key} end
  end
  if #queue > 0 then sendNext() end
end

-- keep these whispers out of your chat window (they still go to the chat log)
local function hide(_, _, msg) return type(msg) == "string" and msg:sub(1, #TAG) == TAG end
local addFilter = ChatFrame_AddMessageEventFilter or (ChatFrameUtil and ChatFrameUtil.AddMessageEventFilter)
if addFilter then
  addFilter("CHAT_MSG_WHISPER", hide)
  addFilter("CHAT_MSG_WHISPER_INFORM", hide)
end

local f = CreateFrame("Frame")
f:RegisterEvent("PLAYER_LOGIN")
f:SetScript("OnEvent", function()
  if not LoggingChat() then LoggingChat(true) end
  C_Timer.NewTicker(2, function() check(false) end)
end)

SLASH_TAYNOTE1 = "/taynote"
SlashCmdList.TAYNOTE = function()
  local note = NSRT and type(NSRT.StoredSharedReminder) == "string" and NSRT.StoredSharedReminder or ""
  local lines, order = sections(note)
  local parts = {}
  for _, enc in ipairs(order) do parts[#parts + 1] = ("boss %s: %d lines"):format(enc, #lines[enc]) end
  print(("Taynalysis Notes: chat log %s; raid note: %s%s."):format(LoggingChat() and "on" or "OFF",
    #parts > 0 and table.concat(parts, ", ") or "none received yet",
    #parts > 0 and (locked() and "; can't send during an encounter, will send after" or "; sending it again now") or ""))
  if not sending then check(true) end
end
