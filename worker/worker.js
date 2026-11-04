// Telegram bot front desk on Cloudflare Workers (free). Replies instantly.
//  - normal messages  -> answered by Gemini, like an AI assistant
//  - Approve / Reject -> starts the GitHub workflow that uploads (or skips) the video
//  - /idea            -> starts the GitHub workflow that makes a new Short
// Only your own Telegram chat is served. Everyone else is ignored.

const HELP =
  "Hi! I'm your Shorts assistant.\n\n" +
  "• Just write to me and I'll answer, in Telugu or English.\n" +
  "• /idea makes a new Short (about 4 minutes). Add a story name to choose it, e.g. /idea The Enemy Who Loved.\n" +
  "• I remember our chat and your instructions (/notes shows them, /forget clears them).\\n" +
  "• /approve 3 uploads Short #3, /reject 3 skips it.\n" +
  "• You can also tap the Approve / Reject buttons under a video.";

async function tg(env, method, params) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(params),
  });
  const data = await r.json().catch(() => ({}));
  if (!data.ok) console.log(`Telegram ${method} failed: ${r.status} ${data.description || ""}`);
  return data;
}

async function say(env, chatId, text, extra = {}) {
  // Telegram allows 4096 characters per message
  for (let i = 0; i < Math.max(text.length, 1); i += 4000) {
    await tg(env, "sendMessage", { chat_id: chatId, text: text.slice(i, i + 4000), ...extra });
  }
}

async function dispatch(env, workflowFile, inputs) {
  const r = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${workflowFile}/dispatches`,
    {
      method: "POST",
      headers: {
        authorization: `Bearer ${env.GITHUB_TOKEN}`,
        accept: "application/vnd.github+json",
        "user-agent": "shorts-bot-worker",
        "x-github-api-version": "2022-11-28",
      },
      body: JSON.stringify({ ref: "main", inputs }),
    }
  );
  if (r.status !== 204) {
    console.log(`GitHub dispatch ${workflowFile} failed: ${r.status}`);
    throw new Error(`GitHub said ${r.status}: ${(await r.text()).slice(0, 200)}`);
  }
}

const SYSTEM =
  "You are the owner's personal AI assistant inside a Telegram bot that runs their Telugu YouTube Shorts " +
  "channel (Ramayanam and Mahabharatam stories told as multi-part animated-style series). Chat about anything, like a " +
  "general assistant, and ALWAYS use the earlier messages of this conversation: remember what the user said, " +
  "follow their instructions in later replies, and never ask them to repeat something they already told you. " +
  "Reply in the language the user writes in (Telugu or English). Be clear and reasonably short. If unsure of a " +
  "fact, say so.\n\n" +
  "You reply with ONLY a JSON object: {\"reply\": \"text for the user\", \"action\": null or " +
  "{\"type\": \"make\", \"topic\": \"story name or empty\"}, \"remember\": \"\"}.\n" +
  "- action make: use ONLY when the user clearly asks you to make/start/continue a Short or the next part now. " +
  "The channel makes one part per run; with an empty topic it continues the current story or picks a new one.\n" +
  "- remember: when the user gives a STANDING instruction about how videos or the bot should be from now on " +
  "(for example voice, style, story types, length, language, what to avoid), put it here as one short sentence; " +
  "otherwise leave it empty. These notes are applied to every future video.\n" +
  "- You cannot approve or reject uploads yourself: tell the user to tap the Approve/Reject buttons or send " +
  "/approve N or /reject N.\n" +
  "- Never claim to have done something you did not do.";

const HISTORY_TURNS = 24;

async function loadJson(env, key, fallback) {
  if (!env.CHAT) return fallback;
  try {
    const v = await env.CHAT.get(key);
    return v ? JSON.parse(v) : fallback;
  } catch (e) {
    console.log(`KV read ${key} failed: ${e.message}`);
    return fallback;
  }
}

async function saveJson(env, key, value) {
  if (!env.CHAT) return;
  try {
    await env.CHAT.put(key, JSON.stringify(value));
  } catch (e) {
    console.log(`KV write ${key} failed: ${e.message}`);
  }
}

async function seriesStatus(env) {
  try {
    const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/contents/series.json`, {
      headers: {
        authorization: `Bearer ${env.GITHUB_TOKEN}`,
        accept: "application/vnd.github.raw+json",
        "user-agent": "shorts-bot-worker",
      },
    });
    if (!r.ok) return "";
    const a = (await r.json()).active;
    if (!a) return "No story is in progress; the next Short starts a new story.";
    if (a.next_part > a.total) return `The story "${a.topic}" is finished (all ${a.total} parts made).`;
    return `Current story: "${a.topic}", ${a.total} parts. Parts made so far: ${a.next_part - 1}. Next to make: Part ${a.next_part}.`;
  } catch (e) {
    return "";
  }
}

function parseModelReply(text) {
  const cleaned = text.replace(/^```(?:json)?|```$/gm, "").trim();
  try {
    const o = JSON.parse(cleaned);
    if (o && typeof o.reply === "string") return o;
  } catch (e) {}
  return { reply: text, action: null, remember: "" };
}

async function askGemini(env, history, notes, status) {
  const models = [env.GEMINI_MODEL || "gemini-3.6-flash", "gemini-3.8-flash"];
  const context =
    (notes.length ? `Standing instructions from the user (always follow them):\n- ${notes.join("\n- ")}\n\n` : "") +
    (status ? `Channel status: ${status}\n\n` : "");
  let lastError = "no reply";
  for (const model of models) {
    try {
      const r = await fetch(
        `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`,
        {
          method: "POST",
          headers: { "content-type": "application/json", "x-goog-api-key": env.GEMINI_API_KEY },
          body: JSON.stringify({
            systemInstruction: { parts: [{ text: SYSTEM + "\n\n" + context }] },
            contents: history.map((m) => ({ role: m.role, parts: [{ text: m.text }] })),
            generationConfig: { responseMimeType: "application/json" },
          }),
        }
      );
      const data = await r.json();
      const reply = data?.candidates?.[0]?.content?.parts?.map((p) => p.text || "").join("").trim();
      if (reply) return parseModelReply(reply);
      lastError = data?.error?.message || "empty answer";
      console.log(`Gemini ${model} gave no answer: ${lastError}`);
    } catch (e) {
      lastError = String(e);
    }
  }
  throw new Error(lastError);
}

async function chat(env, chatId, text) {
  const key = `hist:${chatId}`;
  const history = await loadJson(env, key, []);
  const notes = await loadJson(env, `notes:${chatId}`, []);
  history.push({ role: "user", text });
  const out = await askGemini(env, history.slice(-HISTORY_TURNS), notes, await seriesStatus(env));
  if (out.remember && String(out.remember).trim()) {
    const n = String(out.remember).trim().slice(0, 300);
    if (!notes.includes(n)) notes.push(n);
    await saveJson(env, `notes:${chatId}`, notes.slice(-20));
  }
  let reply = out.reply;
  if (out.action && out.action.type === "make") {
    const topic = String(out.action.topic || "").slice(0, 200);
    try {
      await dispatch(env, "daily.yml", { topic, notes: notes.join(" | ").slice(0, 900) });
      reply += "\n\n▶️ Started. The preview arrives in about 4 minutes.";
    } catch (e) {
      reply += `\n\n⚠️ Could not start it: ${e.message}`;
    }
  }
  history.push({ role: "model", text: reply });
  await saveJson(env, key, history.slice(-HISTORY_TURNS));
  return reply;
}

async function onCallback(env, cq) {
  const chatId = cq.message?.chat?.id;
  console.log(`button tap from chat ${chatId}: ${cq.data}`);
  if (String(chatId) !== String(env.ALLOWED_CHAT_ID)) {
    console.log(`Ignored: chat ${chatId} is not ALLOWED_CHAT_ID (${env.ALLOWED_CHAT_ID})`);
    return;
  }
  await tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: "Got it ✔" });
  const [action, id] = String(cq.data || "").split(":");
  if (!["up_ok", "up_no"].includes(action) || !/^\d+$/.test(id || "")) return;
  await tg(env, "editMessageReplyMarkup", {
    chat_id: chatId,
    message_id: cq.message.message_id,
    reply_markup: { inline_keyboard: [] },
  });
  try {
    await dispatch(env, "poll.yml", {
      action,
      draft_id: id,
      message_id: String(cq.message.message_id),
    });
    await say(env, chatId, action === "up_ok" ? `✅ Approved Short #${id}. Uploading now, I'll send the link.` : `Rejected Short #${id}.`);
  } catch (e) {
    await say(env, chatId, `⚠️ Could not start the upload: ${e.message}`);
  }
}

async function onMessage(env, msg) {
  const chatId = msg.chat?.id;
  console.log(`message from chat ${chatId}: ${(msg.text || "").slice(0, 30)}`);
  if (String(chatId) !== String(env.ALLOWED_CHAT_ID)) {
    console.log(`Ignored: chat ${chatId} is not ALLOWED_CHAT_ID (${env.ALLOWED_CHAT_ID})`);
    return;
  }
  const text = (msg.text || "").trim();
  if (!text) {
    await say(env, chatId, "I can read text messages for now.");
    return;
  }
  const cmd = text.split(/\s+/);
  const name = cmd[0].toLowerCase().replace(/@\w+$/, "");

  try {
    if (name === "/start" || name === "/help") {
      await say(env, chatId, HELP);
    } else if (name === "/forget") {
      if (env.CHAT) {
        await env.CHAT.delete(`hist:${chatId}`);
        await env.CHAT.delete(`notes:${chatId}`);
      }
      await say(env, chatId, "Done. I cleared our chat history and the saved instructions.");
    } else if (name === "/notes") {
      const notes = await loadJson(env, `notes:${chatId}`, []);
      await say(env, chatId, notes.length ? "Saved instructions:\n- " + notes.join("\n- ") : "No saved instructions yet.");
    } else if (name === "/idea") {
      await say(env, chatId, "On it, making a new Short. I'll send the preview in about 4 minutes.");
      const topic = text.slice(cmd[0].length).trim().slice(0, 200);
      const notes = await loadJson(env, `notes:${chatId}`, []);
      await dispatch(env, "daily.yml", { topic, notes: notes.join(" | ").slice(0, 900) });
    } else if (name === "/approve" || name === "/reject") {
      if (!/^\d+$/.test(cmd[1] || "")) {
        await say(env, chatId, `Send it like this: ${name} 3`);
        return;
      }
      const approve = name === "/approve";
      await dispatch(env, "poll.yml", { action: approve ? "up_ok" : "up_no", draft_id: cmd[1], message_id: "0" });
      await say(env, chatId, approve ? `✅ Approved Short #${cmd[1]}. Uploading now, I'll send the link.` : `Rejected Short #${cmd[1]}.`);
    } else if (name.startsWith("/")) {
      await say(env, chatId, "I don't know that command. Send /help.");
    } else {
      await tg(env, "sendChatAction", { chat_id: chatId, action: "typing" });
      await say(env, chatId, await chat(env, chatId, text));
    }
  } catch (e) {
    console.log(`Error: ${e.message}`);
    await say(env, chatId, `⚠️ ${e.message}`);
  }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // One-time setup: open  https://<your-worker>/setup?key=<WEBHOOK_SECRET>  in a browser
    if (request.method === "GET" && url.pathname === "/setup") {
      if (url.searchParams.get("key") !== env.WEBHOOK_SECRET) return new Response("Wrong key", { status: 403 });
      const result = await tg(env, "setWebhook", {
        url: `${url.origin}/`,
        secret_token: env.WEBHOOK_SECRET,
        allowed_updates: ["message", "callback_query"],
        drop_pending_updates: false,
      });
      return new Response(result.ok ? "Webhook is set. Message your bot now." : `Telegram said: ${JSON.stringify(result)}`);
    }

    if (request.method !== "POST") return new Response("Shorts bot is running.");
    if (request.headers.get("x-telegram-bot-api-secret-token") !== env.WEBHOOK_SECRET) {
      console.log("Rejected: secret token from Telegram does not match WEBHOOK_SECRET");
      return new Response("Forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("ok");
    }
    console.log(`update received: ${update.callback_query ? "button" : update.message ? "message" : "other"}`);
    // answer Telegram straight away, do the work afterwards
    if (update.callback_query) ctx.waitUntil(onCallback(env, update.callback_query));
    else if (update.message) ctx.waitUntil(onMessage(env, update.message));
    return new Response("ok");
  },
};
