# Message Flows (EN + 中文)

> Sample dialogues for the 8+2 corner cases the bot handles. Each dialogue shows
> a representative exchange. The first row in each pair is **EN**, the second
> row is **中文** (`zh`). See [architecture.md](architecture.md) for the
> high-level flow and [runbook.md](runbook.md) for what to do when one
> misbehaves.

## 1. FAQ — opening hours

**EN**

> Customer: What time do you open on weekends?
> Bot: We're open Saturday and Sunday 09:00–17:00. Last tour starts at 16:00. Want to book a slot?

**中文**

> 客户: 周末几点开门？
> 机器人: 我们的营业时间是周六和周日 09:00–17:00。最后一团 16:00 出发。需要预约吗？

## 2. New booking — happy path

**EN**

> Customer: Hi, can we book a tour for 30 primary-5 kids on 15 Aug 2026 at 10:30?
> Bot: Got it — 30 pax, 15 Aug 2026, 10:30. I just need a contact name + email to lock it in.
> Customer: Jane Doe, jane@acme.edu
> Bot: Here's what I'll book: **15 Aug 2026 (Sat) 10:30, 30 pax, contact Jane Doe <jane@acme.edu>**. Reply **YES** to confirm or **NO** to cancel.
> Customer: YES
> Bot: Booked. Confirmation #AQMk…. A SGD 200 deposit is required within 48h to keep the slot — instructions sent to jane@acme.edu.

**中文**

> 客户: 你好，我想预约 8 月 15 日 上午 10:30，30 位小五学生的参观。
> 机器人: 好的 — 30 位，2026 年 8 月 15 日，10:30。我还需要联系人姓名 + 邮箱。
> 客户: 张老师 zhang@school.cn
> 机器人: 预约信息：**2026 年 8 月 15 日（周六）10:30，30 人，联系人 张老师 <zhang@school.cn>**。回复 **YES** 确认，**NO** 取消。
> 客户: YES
> 机器人: 已预约。编号 #AQMk…。请在 48 小时内支付 SGD 200 定金，详情已发到 zhang@school.cn。

## 3. New booking — slot not available

**EN**

> Customer: Can we come 12 Aug 2026 09:00? Group of 50.
> Bot: Sorry, 09:00 on 12 Aug is at capacity. The next free slot that day is 14:30. Want me to check 14:30, or pass this to the team for a custom slot?
> Customer: Pass to the team.
> Bot: Done — the team will reach out shortly. For immediate help, contact +65XXXXXXXX.

**中文**

> 客户: 我们 8 月 12 日 09:00 可以来吗？50 人。
> 机器人: 抱歉，8 月 12 日 09:00 已满。当天最近的可预约时段是 14:30。要我查 14:30，还是转给团队安排其他时段？
> 客户: 转给团队。
> 机器人: 已转交，团队会尽快联系您。如需紧急协助，请拨打 +65XXXXXXXX。

## 4. New booking — full data collection (multi-turn)

> Use case: customer never volunteers email in the first message; bot asks
> twice before falling back to handoff. Tests `book_new` field collection
> and the handoff fallback.

**EN**

> Customer: book a tour for 20 pax 20 Aug afternoon
> Bot: Sure — afternoon of 20 Aug 2026, 20 pax. The afternoon slot is 14:30. Could I have a contact name?
> Customer: Mr Lee
> Bot: Thanks. And an email to send the deposit instructions to?
> Customer: no email just WhatsApp
> Bot: I need an email to confirm the booking — it's where we send the deposit instructions. Reply **SKIP** to pass this to the team instead.
> Customer: SKIP
> Bot: Done — the team will reach out on WhatsApp to finish the booking. For immediate help, contact +65XXXXXXXX.
> [admin receives WA message: phone, transcript, `reason=missing_required_field`]

**中文**

> 客户: 帮我预约 8 月 20 日 下午 20 人
> 机器人: 好的 — 2026 年 8 月 20 日 下午，20 人。下午的开放时段是 14:30。请问联系人姓名？
> 客户: 李先生
> 机器人: 谢谢。请问邮箱（定金通知会发到邮箱）？
> 客户: 没有邮箱，用 WhatsApp 联系就行
> 机器人: 完成预约需要邮箱，定金通知会发到邮箱。回复 **SKIP** 可改由团队在 WhatsApp 上跟进。
> 客户: SKIP
> 机器人: 已转交，团队会在 WhatsApp 上跟您完成预约。如需紧急协助，请拨打 +65XXXXXXXX。
> [admin 收到 WhatsApp：来电号码、对话记录、`reason=missing_required_field`]

## 5. Edit booking

**EN**

> Customer: I need to change the date of our booking to 17 Aug.
> Bot: I found one booking on your number — 15 Aug 10:30, 30 pax. What new date and time?
> Customer: 17 Aug 10:30
> Bot: Change **15 Aug 10:30 → 17 Aug 10:30**? Reply **YES** to confirm.
> Customer: yes
> Bot: Sorry, I need a plain **YES** (uppercase) to confirm. Reply **YES** to proceed.
> Customer: YES
> Bot: Done. Updated to 17 Aug 10:30, same pax. New confirmation #AQMk….

**中文**

> 客户: 我想改一下预约日期，改到 8 月 17 日。
> 机器人: 查到您的号码下有一单 — 8 月 15 日 10:30，30 人。请问要改成什么日期和时间？
> 客户: 8 月 17 日 10:30
> 机器人: 改为 **8 月 17 日 10:30**（其他不变）？回复 **YES** 确认。
> 客户: 好的
> 机器人: 抱歉，确认请回复大写 **YES**。
> 客户: YES
> 机器人: 已改。最新 8 月 17 日 10:30，人数不变。编号 #AQMk…。

## 6. Cancel booking

**EN**

> Customer: Please cancel the 15 Aug tour.
> Bot: Found it — 15 Aug 10:30, 30 pax. Reply **YES** to cancel (this is irreversible). Any other reply = keep it.
> Customer: YES
> Bot: Cancelled. Confirmation #AQMk…. The deposit refund (if any) will be processed per the original terms.

**中文**

> 客户: 麻烦取消 8 月 15 日的参观。
> 机器人: 找到了 — 8 月 15 日 10:30，30 人。回复 **YES** 取消（不可撤销）。回复其他内容 = 保留预约。
> 客户: YES
> 机器人: 已取消。编号 #AQMk…。定金退款（如有）按原条款处理。

## 7. Handoff — refund dispute

**EN**

> Customer: I want a refund. The tour guide was rude.
> Bot: I've flagged this for the team — they'll be in touch shortly. For immediate help, contact +65XXXXXXXX.

**中文**

> 客户: 我要退款。导游态度很差。
> 机器人: 已经转给团队处理，他们会尽快联系您。如需紧急协助，请拨打 +65XXXXXXXX。

## 8. Handoff — abuse

**EN**

> Customer: <abusive message>
> Bot: I'm not able to help with that. The team has been notified.
> [admin receives WA message with caller phone + message + `reason=abuse`]

**中文**

> 客户: <辱骂性消息>
> 机器人: 抱歉，这边无法处理，已通知团队。
> [admin 收到 WhatsApp：来电号码、原文、`reason=abuse`]

## 9. Image question

> Use case: customer sends a photo (school gate, prior invoice, signage) and
> asks what we think. Tests the `image` pipeline in `wa-bridge/src/image.ts`
> and the LLM's ability to use the photo as context for an FAQ answer.

**EN**

> Customer: <sends photo of a school sign> — is this your entrance?
> Bot: That looks like a covered walkway on the public side, not the tour entrance. The tour entrance is the **green gate on the right after the car park** — see the map I just sent. Want me to text a copy of the directions?
> Customer: yes please
> Bot: Sent. Anything else?

**中文**

> 客户: <发送学校指示牌照片> — 这是你们的入口吗？
> 机器人: 照片上像是公共区域的有顶走道，不是参观入口。参观入口在**停车场右手边的绿色大门** —— 我刚发了地图。要不要把路线文字版也发一份？
> 客户: 好的
> 机器人: 已发送。还有其他需要吗？

## 10. Mid-flow language switch

> Use case: customer starts a booking flow in English, then switches to
> 中文 in the middle. Tests `state.language` updates on every turn (see
> [architecture.md](architecture.md#multilingual)).

**EN / 中文**

> Customer (EN): hi, book a tour for 25 pax 22 Sep 10:30
> Bot: Got it — 25 pax, 22 Sep 2026, 10:30. Contact name + email?
> Customer (中文): 王老师 wang@school.cn
> Bot: 预约信息：**2026 年 9 月 22 日 10:30，25 人，联系人 王老师 <wang@school.cn>**。回复 **YES** 确认，**NO** 取消。
> Customer (中文): YES
> Bot: 已预约。编号 #AQMk…。请在 48 小时内支付 SGD 200 定金，详情已发到 wang@school.cn。

---

## Coverage matrix

| # | Dialogue | EN | 中文 | Flow |
|---|---|---|---|---|
| 1 | FAQ | ✓ | ✓ | `faq` |
| 2 | New booking — happy | ✓ | ✓ | `book_new` |
| 3 | New booking — unavailable | ✓ | ✓ | `book_new` → `handoff` |
| 4 | New booking — full multi-turn | ✓ | ✓ | `book_new` → `handoff(missing_required_field)` |
| 5 | Edit booking | ✓ | ✓ | `book_edit` |
| 6 | Cancel booking | ✓ | ✓ | `book_cancel` |
| 7 | Handoff — refund dispute | ✓ | ✓ | `handoff` |
| 8 | Handoff — abuse | ✓ | ✓ | `handoff(reason=abuse)` |
| 9 | Image question | ✓ | ✓ | `faq` w/ image |
| 10 | Mid-flow language switch | ✓ | ✓ | `book_new` |

**Total: 10 dialogues × 2 languages = 20 entries** (acceptance: ≥ 8 × 2 = 16).

---

## Extended dialogue set (issue #15)

Eight flows, each shown in both EN and 中文. These are the canonical
test inputs for `make smoke` and the in-process e2e suite.

### 1. FAQ

**EN**
```
Customer: What time do you open?
Bot: We're open Saturday and Sunday 09:00–17:00. Last tour starts at 16:00.
```

**中文**
```
客户: 几点开门？
机器人: 我们的营业时间是周六和周日 09:00–17:00。最后一团 16:00 出发。
```

### 2. New booking — happy path

**EN**
```
Customer: Can we book 30 kids on 15 Aug 2026 at 10:30?
Bot: Got it — 30 pax on 2026-08-15. I just need a contact email to lock it in.
Customer: jane@school.cn
Bot: Here's what I'll book: 2026-08-15 10:30, 30 pax, contact jane@school.cn. Reply YES to confirm.
Customer: YES
Bot: Booked. Confirmation #AQMk…1234.
```

**中文**
```
客户: 我们要预约 8月15日 10:30，30 位小五学生。
机器人: 好的，30 位，2026 年 8 月 15 日。请问联系邮箱？
客户: zhang@school.cn
机器人: 预约信息：2026 年 8 月 15 日 10:30，30 位，联系人 zhang@school.cn。回复 YES 确认。
客户: 是
机器人: 已预约。编号 #AQMk…5678.
```

### 3. New booking — unavailable slot

**EN**
```
Customer: 12 Aug 2026 09:00 for 50 people.
Bot: Sorry, 12 Aug 09:00 is at capacity. The next free slot that day is 14:30. Want me to try that, or hand this over to the team?
```

**中文**
```
客户: 8月12日 09:00，50 位。
机器人: 抱歉，8 月 12 日 09:00 已满。当天还有 14:30。要试 14:30，还是转给团队？
```

### 4. New booking — full multi-turn

**EN**
```
Customer: I want to book a tour.
Bot: What date would you like? (YYYY-MM-DD)
Customer: 15 Aug
Bot: What time? (HH:MM, 24-hour)
Customer: 10:30
Bot: How many people?
Customer: 30
Bot: Contact email or phone? (at least one)
Customer: jane@school.cn
Bot: I'll book: 2026-08-15 10:30, 30 pax, contact jane@school.cn. Reply YES to confirm.
Customer: YES
Bot: Booked. Confirmation #AQMk…. Deposit SGD 200 due 48h before.
```

**中文**
```
客户: 我要预约参观。
机器人: 请问您想预约哪一天？（YYYY-MM-DD）
客户: 8月15日
机器人: 请问几点？（24 小时制，HH:MM）
客户: 10:30
机器人: 请问有多少人？
客户: 30
机器人: 联系邮箱或电话？（至少填一个）
客户: zhang@school.cn
机器人: 预约信息：2026-08-15 10:30，30 位，联系人 zhang@school.cn。回复 YES 确认。
客户: 是
机器人: 已预约。编号 #AQMk…. 定金 SGD 200，需在参观前 48 小时支付。
```

### 5. Edit booking

**EN**
```
Customer: I need to change my booking.
Bot: I found this booking on your number: 2026-08-15 10:30, 30 pax. Reply YES to edit, or NO to leave it as-is.
Customer: YES
Bot: Which would you like to change — date, time, or pax?
Customer: 14:30
Bot: I'll change 2026-08-15 10:30 → 2026-08-15 14:30. Reply YES to confirm.
Customer: YES
Bot: Updated. New confirmation #AQMk…9876.
```

**中文**
```
客户: 我想改预约时间。
机器人: 我在您的号码下找到这个预约：2026-08-15 10:30, 30 位。回复 YES 修改，回复 NO 保持不变。
客户: 是
机器人: 您想修改哪一项？日期、时间，还是人数？
客户: 14:30
机器人: 我将把 2026-08-15 10:30 改为 2026-08-15 14:30。回复 YES 确认。
客户: 是
机器人: 已修改。新编号 #AQMk…9876。
```

### 6. Cancel booking

**EN**
```
Customer: Please cancel our tour.
Bot: I found this booking on your number: 2026-08-15 10:30, 30 pax. I'll cancel this. This is irreversible. Reply YES to confirm.
Customer: YES
Bot: Cancelled. Confirmation #AQMk…1234.
```

**中文**
```
客户: 请帮我取消预约。
机器人: 我在您的号码下找到这个预约：2026-08-15 10:30, 30 位。我将取消这个预约。此操作不可撤销。回复 YES 确认。
客户: 是
机器人: 已取消。编号 #AQMk…1234。
```

### 7. Handoff — refund

**EN**
```
Customer: I want a refund.
Bot: I've flagged this for the team — they'll be in touch shortly. For immediate help, contact +6591234567.
```

**中文**
```
客户: 我要退款。
机器人: 已经转给团队处理，他们会尽快联系您。如需紧急协助，请拨打 +6591234567。
```

### 8. Handoff — abuse

**EN**
```
Customer: <abusive message>
Bot: I'm not able to help with that. The team has been notified.
[admin receives: 🚨 [HANDOFF] ABUSE / Customer: +6590000099 / Message: <abusive content>]
```

**中文**
```
客户: <辱骂内容>
机器人: 抱歉，我无法处理。已通知团队。
[管理员收到: 🚨 [转人工] 辱骂 / 客户: +6590000099 / 内容: <辱骂内容>]
```

### 9. Image question

**EN**
```
Customer: <sends photo of a goat> what breed is this?
Bot: Got the photo. I'll look it up.
Bot: [after rag_qdrant.ask_with_photo returns an answer] That's a Saanen — common dairy breed at SAAC FARM.
```

**中文**
```
客户: <发送山羊图片> 这是什么品种？
机器人: 收到图片了。我这边有一张图片，路径 /p/goat.jpg。
机器人: [rag_qdrant.ask_with_photo 返回结果] 这是萨能奶山羊——SAAC FARM 的常见奶用品种。
```

### 10. Mid-flow language switch

**EN → 中文**
```
Customer: Can I book for tomorrow?
Bot: What time? (HH:MM, 24-hour)
Customer: 下午3点
Bot: 请问几点？（24 小时制，HH:MM）
Customer: 15:00
Bot: How many people?
Customer: 10 位
Bot: 请问有多少人？
Customer: 10
Bot: Contact email or phone? (at least one)
Customer: me@example.com
Bot: I'll book: <date> 15:00, 10 pax, contact me@example.com. Reply YES to confirm.
```

**中文 → EN**
```
客户: 我要预约明天参观。
机器人: 请问您想预约哪一天？（YYYY-MM-DD）
客户: tomorrow
Bot: What date would you like? (YYYY-MM-DD)
Customer: 2026-08-25
Bot: I'll book: 2026-08-25 <time>, <pax>, contact <email>. Reply YES to confirm.
```

