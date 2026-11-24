# Oshigoe

[English](#english)

YouTube や Twitch の動画・ライブ配信を再生しながら、声と背景音の音量を個別に調整できるアプリです。

音声分離には [Inworld Voice Isolator](https://huggingface.co/inworld/voice-isolator) を使用しています。

## ユースケース

- 配信者の声と背景音の音量バランスを好みに合わせて調整したい場合
- 視聴者参加型の配信に参加するとき、配信側のゲーム音を抑えたい場合

## 起動要件

| OS | 要件 |
| --- | --- |
| Windows | NVIDIA GPU、ドライバー 580 以降 |
| macOS | Apple Silicon 搭載の Mac |

## 起動する

**注意** 初回起動時は、実行に必要なプログラムや音声分離モデルなど、数 GB のファイルをダウンロードします。インターネットに接続した状態で実行してください。

1. このフォルダーの起動スクリプトを実行します。Windows は [`start.win.cmd`](start.win.cmd)、macOS は [`start.mac.command`](start.mac.command) を使用します。
2. 起動が完了するまで待機します。
3. ターミナルの `Listening URLs` に表示された URL をブラウザーで開きます。

スマートフォンなど別の端末で視聴する場合は、サーバーを `start-shared` で起動してください。OS 別の起動方法と視聴手順は [別の端末から接続する](#別の端末から接続する) を参照してください。

サーバーを終了するには、ターミナルで Ctrl+C を押下してください。

同じフォルダーからサーバーを複数起動する場合は、空いているポートを `--port` で指定してください。たとえば、Windows では `.\start.win.cmd --port 48764`、macOS では `./start.mac.command --port 48764` を実行します。

## 動画・ライブ配信を再生する

1. **動画・ライブ配信の URL** に動画またはライブ配信の URL を入力し、**開始**を押下します。
2. 「音量」の「声」と「背景音」を調整します。スライダーを左端に移動するとミュートできます。

Twitch のライブ配信には配信中のチャンネルの URL（`https://www.twitch.tv/チャンネル名`）、アーカイブ動画には動画の URL（`https://www.twitch.tv/videos/動画ID`）を指定します。

通常動画では、シークバーで再生位置を変更し、再生を一時停止・再開できます。再生を終了するには **停止** を押下します。

画面上部の言語メニューで、日本語と英語を切り替えられます。選択した言語はそのブラウザーに保存されます。

### ログインが必要な動画

1. サーバーを実行する端末の Firefox で YouTube または Twitch にログインし、対象の動画を再生できることを確認します。
2. 本アプリの「設定」で「ブラウザのログイン情報を使用」を有効にしてから、**開始**を押下します。

公開動画でエラーになる場合も、この設定を有効にして再試行してみてください。

## 別の端末から接続する

**注意** この方法で起動すると、同じネットワーク上の他の端末からもアプリにアクセスできます。信頼できる LAN や VPN 内で使用してください。

1. サーバーを実行する端末で、Windows は [`start-shared.win.cmd`](start-shared.win.cmd)、macOS は [`start-shared.mac.command`](start-shared.mac.command) を実行します。
2. 視聴端末をサーバーと同じ LAN に接続し、ブラウザーでターミナルに表示されたサーバーの LAN 内 URL を開きます。たとえば `http://192.168.1.10:48763/` です。
3. 右上に「動作中」と表示されていれば、プレーヤー中央の再生ボタンを押下します。

外出先から視聴する場合は、サーバーと視聴端末を Tailscale などの同じ VPN に接続し、サーバーの VPN 内 URL を開きます。ルーターのポート転送や Tailscale Funnel によるインターネットへの公開は避けてください。 [Tailscale の端末間接続ガイド](https://tailscale.com/docs/how-to/connect-to-devices)

## 設定を変更する

「設定」の各項目はブラウザーに保存され、次に **開始** を押下したときに適用されます。

「音量」は再生中に調整できます。

### 動画の品質

「設定」の「動画の品質」を展開すると、解像度の上限、フレームレートの上限、映像ビットレート、音声ビットレートを指定できます。解像度の上限は、横動画では高さ、縦動画では幅に適用されます。

### 映像遅延

音声が映像より遅れて聞こえる場合は、**開始**を押下する前に「映像遅延」を有効にして遅延時間を設定します。

設定の横にある「?」を押下し、「同期テスト動画の URL をコピー」を押下します。その URL で同期テスト動画を再生し、視聴に使用するスピーカーやイヤホンで映像と音声のズレを確認してください。

## アップデートする

### ZIP 版の場合

1. サーバーを実行しているターミナルで Ctrl+C を押下し、サーバーを停止します。
2. Windows では [`update.win.cmd`](update.win.cmd)、macOS では [`update.mac.command`](update.mac.command) を実行します。
3. 更新が完了したら、同じフォルダーの起動スクリプトを実行します。

### git の場合

サーバーを停止し、プロジェクトのフォルダーで `git pull` を実行してください。ソースを変更して利用する場合も git を使用してください。

## 再生に問題がある場合

| 症状 | 確認・操作 |
| --- | --- |
| サーバーに接続できない | ターミナルに表示された URL を確認します。別端末では、同じ LAN または VPN に接続し、サーバーの接続先 IP アドレスを使用します。視聴には WebRTC の UDP 通信も使用します。 |
| 処理を開始できない | URL と動画の公開状態を確認します。Twitch のチャンネル URL を指定した場合は、チャンネルが配信中か確認します。ログインが必要な動画の場合は「ログインが必要な動画」を参照してください。サーバーを停止して起動スクリプトを再実行すると、動画取得ライブラリの yt-dlp が更新されて接続できることがあります。|
| 音量調整が反映されない | 「ステータス」の「音声処理」 を確認します。準備中や障害からの復旧中は原音が再生され、音声分離の再開後に音量調整が反映されます。 |

アプリの動作中は「ステータス」で、入力形式、映像と音声の処理方式、受信ビットレート、接続品質を確認できます。

## 制限

- ゲーム内のキャラクターボイスや楽曲のボーカルは除去できません。
- 叫び声やエフェクトがかかった声は、正しく分離されない場合があります。
- 音声分離によって声の音質が低下します。

## 技術資料

- [開発ノート](docs/development-notes.md)

## ライセンス

Oshigoe のライセンスは Apache-2.0 です。[ライセンス原文](LICENSE)

依存パッケージ、実行ファイル、モデル、フォントのライセンスと出典は [サードパーティーのライセンス](THIRD_PARTY_NOTICES.md) を参照してください。

---

## English

Hear your oshi's voice. Oshigoe (推し声) is Japanese for "your oshi's voice."

Adjust how loud voices and background sounds are while you watch YouTube or Twitch. Oshigoe separates the audio with [Inworld Voice Isolator](https://huggingface.co/inworld/voice-isolator) so you can set each volume independently.

### Use cases

- Set the balance between the streamer's voice and the background sound to your liking.
- Turn down the game audio coming from the stream when you're playing along.

### Requirements

| OS | Requirements |
| --- | --- |
| Windows | NVIDIA GPU, driver version 580 or later |
| macOS | Mac with Apple Silicon |

### Start the app

**Note** The first launch downloads several gigabytes of files, including required tools and the voice isolation model. Keep your device connected to the internet.

1. Open [`start.win.cmd`](start.win.cmd) on Windows or [`start.mac.command`](start.mac.command) on macOS.
2. Wait for the app to finish starting.
3. In your browser, open one of the addresses under `Listening URLs` in the terminal.

Use `start-shared` to watch from a phone or another device. [Connect from another device](#connect-from-another-device)

To stop the server, press Ctrl+C in the terminal.

To run another server from the same folder, use `--port` to specify an available port. For example, run `.\start.win.cmd --port 48764` on Windows or `./start.mac.command --port 48764` on macOS.

### Play a video or live stream

1. Paste a link into **Video or live stream URL** and press **Start**. For a Twitch stream that's live, paste the channel URL (`https://www.twitch.tv/CHANNEL_NAME`). For a Twitch archive, paste the video URL (`https://www.twitch.tv/videos/VIDEO_ID`).
2. Under **Volume**, adjust **Voice** and **Background**. Move either slider all the way left to mute it.

With recorded videos, use the seek bar to jump to another point. You can also pause and resume. To end playback, press **Stop**.

Your browser remembers the language you choose from the menu at the top of the page. Choose English or Japanese.

#### Videos that require sign-in

1. Sign in to YouTube or Twitch in Firefox on the server device. Make sure the video plays in Firefox.
2. Turn on **Use browser sign-in** in **Settings**, then press **Start**.

You can also try **Use browser sign-in** when a public video fails to play.

### Connect from another device

**Note** When you start the server this way, other devices on the network can reach the app. Use a trusted LAN or VPN.

1. On the server device, open [`start-shared.win.cmd`](start-shared.win.cmd) on Windows or [`start-shared.mac.command`](start-shared.mac.command) on macOS.
2. Connect the device you want to watch on to the same LAN. In its browser, open the LAN address shown in the server terminal, for example `http://192.168.1.10:48763/`.
3. When **Running** appears in the upper-right corner, press **Play** in the center of the player.

To watch away from home, connect both devices to the same VPN, such as Tailscale, and use the server's VPN address. Avoid router port forwarding and Tailscale Funnel, which can expose the app to the public internet. [Tailscale guide to connecting devices](https://tailscale.com/docs/how-to/connect-to-devices)

### Change settings

Your browser saves **Settings** and applies them the next time you press **Start**. You can adjust **Volume** during playback.

#### Video quality

Expand **Video quality** in **Settings** to limit the resolution and frame rate or set the video and audio bitrates. For landscape videos, the limit applies to height. For portrait videos, it applies to width.

#### Video delay

If you hear the sound after you see the picture, turn on **Video delay** and set the delay before you press **Start**.

To check the timing, press **?** next to **Video delay**, then press **Copy sync test video URL**. Play that video in the app with your usual speakers or headphones.

### Update the app

#### For a ZIP installation

1. Stop the server by pressing Ctrl+C in its terminal.
2. Run [`update.win.cmd`](update.win.cmd) on Windows or [`update.mac.command`](update.mac.command) on macOS.
3. Once the update finishes, run the start script from the same folder.

#### For git

Stop the server, then run `git pull` in the project folder. Use git if you modify the source.

### Troubleshoot playback

| Symptom | What to check or do |
| --- | --- |
| The app won't connect | Check the URL in the server terminal. On another device, join the same LAN or VPN and use the server's address on that network. WebRTC uses UDP for playback. |
| The app can't start processing the video | Check the URL and whether you can access the video. For Twitch channel links, verify that the channel is live. Restarting the server with the startup script updates yt-dlp, which may fix the problem. [Videos that require sign-in](#videos-that-require-sign-in) |
| The volume controls have no effect | Under **Status**, check **Audio processing**. The app plays the original audio while voice isolation starts or recovers. The volume controls take effect once voice isolation is active. |

While the app is running, **Status** shows the source format, the video and audio processing modes, incoming bitrates, and connection quality.

### Limitations

- In-game character voices and vocals in music remain audible after voice isolation.
- Shouting or voices altered by audio effects may not be separated correctly.
- Voice isolation reduces voice quality.

### Technical documentation

- [Development notes](docs/development-notes.md) (in Japanese)

### License

Oshigoe is licensed under Apache-2.0. [License text](LICENSE)

See [Third-party licenses](THIRD_PARTY_NOTICES.md) for the licenses and sources of the app's dependencies, executables, models, and fonts.
