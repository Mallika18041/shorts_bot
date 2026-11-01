// Telegram bot front desk on Cloudflare Workers (free). Replies instantly.
//  - normal messages  -> answered by Gemini, like an AI assistant
//  - Approve / Reject -> starts the GitHub workflow that uploads (or skips) the video
//  - /idea            -> starts the GitHub workflow that makes a new Short
// Only your own Telegram chat is served. Everyone else is ignored.

const HELP =
  "Hi! I'm your Shorts assistant.\n\n" +
  "• Just write to me and I'll answer, in Telugu or English.\n" +
  "• /idea makes a new Short (about 4 minutes).\n" +
  "• /approve 3 uploads Short #3, /reject 3 skips it.\n" +
  "• You can also tap the Approve / Reject buttons under a video.";

async function tg(env, method, params) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(params),
  });
  return r.json().catch(() => ({}));
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
    throw new Error(`GitHub said ${r.status}: ${(await r.text()).slice(0, 200)}`);
  }
}

const SYSTEM =
  "You are a friendly, capable AI assistant inside a Telegram bot that also runs a Telugu YouTube " +
  "Shorts channel (historical love stories). Chat about anything the user asks, like a general " +
  "assistant. Reply in the language the user writes in (Telugu or English). Be clear and reasonably " +
  "short. If you are not sure of a fact, say so instead of guessing.";

async function askGemini(env, text) {
  const models = [env.GEMINI_MODEL || "gemini-3.6-flash", "gemini-3.8-flash"];
  let lastError = "no reply";
  for (const model of models) {
    try {
      const r = await fetch(
        `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`,
        {
          method: "POST",
          headers: { "content-type": "application/json", "x-goog-api-key": env.GEMINI_API_KEY },
          body: JSON.stringify({
            systemInstruction: { parts: [{ text: SYSTEM }] },
            contents: [{ role: "user", parts: [{ text }] }],
          }),
        }
      );
      const data = await r.json();
      const reply = data?.candidates?.[0]?.content?.parts?.map((p) => p.text || "").join("").trim();
      if (reply) return reply;
      lastError = data?.error?.message || "empty answer";
    } catch (e) {
      lastError = String(e);
    }
  }
  throw new Error(lastError);
}

async function onCallback(env, cq) {
  const chatId = cq.message?.chat?.id;
  if (String(chatId) !== String(env.ALLOWED_CHAT_ID)) return;
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
  if (String(chatId) !== String(env.ALLOWED_CHAT_ID)) return;
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
    } else if (name === "/idea") {
      await say(env, chatId, "On it, making a new Short. I'll send the preview in about 4 minutes.");
      await dispatch(env, "daily.yml", {});
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
      await say(env, chatId, await askGemini(env, text));
    }
  } catch (e) {
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
      return new Response("Forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("ok");
    }
    // answer Telegram straight away, do the work afterwards
    if (update.callback_query) ctx.waitUntil(onCallback(env, update.callback_query));
    else if (update.message) ctx.waitUntil(onMessage(env, update.message));
    return new Response("ok");
  },
};
