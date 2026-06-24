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
