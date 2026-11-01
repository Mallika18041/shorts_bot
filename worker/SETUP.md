# Instant replies: Cloudflare Worker setup (about 20 minutes, free)

What you get: the bot answers every message right away like an AI assistant (Gemini), and your
Approve / Reject taps start the upload within a minute instead of waiting for the 30-minute check.

You need: a Cloudflare account (free), a GitHub token, and the keys you already have.
Never paste tokens or keys into a chat. They go only into the boxes below.

## Part A. A GitHub token (so the Worker can start your workflows)
1. Open https://github.com/settings/personal-access-tokens/new (sign in).
2. Token name: `shorts-bot-worker`. Expiration: 90 days (put a reminder to renew it).
3. Repository access: **Only select repositories**, then choose `shorts_bot`.
4. Permissions > Repository permissions > **Actions: Read and write**. Leave everything else.
5. Tap **Generate token** and copy it. You see it only once.

## Part B. Create the Worker
1. Go to https://dash.cloudflare.com and sign up (free).
2. **Workers & Pages** > **Create** > **Create Worker**. Name it `shorts-bot`. Tap **Deploy**.
3. Tap **Edit code**. Delete everything in the editor, paste the whole content of `worker/worker.js`
   from this repo, then tap **Deploy**.
4. Note your Worker address, like `https://shorts-bot.YOURNAME.workers.dev`.

## Part C. Add the settings
Worker > **Settings** > **Variables and Secrets** > **Add**.

Type **Text**:
| Name | Value |
|---|---|
| `ALLOWED_CHAT_ID` | `8179151039` |
| `GITHUB_REPO` | `Mallika18041/shorts_bot` |
| `GEMINI_MODEL` | the Gemini model name your project uses (optional) |

Type **Secret**:
| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | your bot token from @BotFather (`/mybots` > your bot > API Token) |
| `GITHUB_TOKEN` | the token from Part A |
| `GEMINI_API_KEY` | the same Gemini key as your GitHub secret |
| `WEBHOOK_SECRET` | make up a long password with only letters, numbers, `_` or `-` (at least 20 characters) |

Tap **Deploy** after adding them.

## Part D. Connect Telegram to the Worker
Open this in your browser (use your own address and your WEBHOOK_SECRET):

`https://shorts-bot.YOURNAME.workers.dev/setup?key=YOUR_WEBHOOK_SECRET`

It should say **Webhook is set**.

## Part E. Test
1. Send your bot: `Hello`. It should answer within a few seconds.
2. Send `/help` to see the commands.
3. Send `/idea`. A new Short preview arrives in about 4 minutes. Approve or Reject it with the buttons.

## Good to know
- Only your own chat gets answers. Everyone else is ignored, so nobody can use your Gemini quota.
- Each message is answered on its own. The bot does not remember earlier messages yet.
- Once the webhook is on, Telegram no longer lets GitHub poll for taps. The 30-minute "Handle Telegram taps"
  check then only logs a harmless warning. Approve/Reject go through the Worker instead.
- Gemini's free plan has daily limits. If the bot says it hit a limit, wait or use a different key.
- To turn the Worker off and go back to polling, open
  `https://api.telegram.org/botYOUR_TOKEN/deleteWebhook` in your browser.
- Renew the GitHub token before it expires, or Approve/Reject and /idea will stop working.

## The same setup using commands (PowerShell)
See the chat message that came with this file, or run these in order from the repo folder:

```
winget install OpenJS.NodeJS.LTS            # once; then close and reopen PowerShell
cd worker
npx wrangler login
npx wrangler deploy
npx wrangler secret put TELEGRAM_BOT_TOKEN  # paste when asked (hidden)
npx wrangler secret put GEMINI_API_KEY
npx wrangler secret put GITHUB_TOKEN        # fine-grained token with Actions: Read and write on shorts_bot only
$b = New-Object byte[] 24; (New-Object Security.Cryptography.RNGCryptoServiceProvider).GetBytes($b)
$s = ([BitConverter]::ToString($b) -replace '-','')
$s | npx wrangler secret put WEBHOOK_SECRET
Invoke-RestMethod "https://shorts-bot.YOURNAME.workers.dev/setup?key=$s"
```
