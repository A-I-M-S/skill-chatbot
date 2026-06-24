# Message Flows (EN + 中文)

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

## 4. Edit booking

**EN**

> Customer: I need to change the date of our booking to 17 Aug.
> Bot: I found one booking on your number — 15 Aug 10:30, 30 pax. What new date and time?
> Customer: 17 Aug 10:30
> Bot: Change **15 Aug 10:30 → 17 Aug 10:30**? Reply **YES** to confirm.
> Customer: yes
> Bot: Sorry, I need a plain **YES** (uppercase) to confirm. Reply **YES** to proceed.
> Customer: YES
> Bot: Done. Updated to 17 Aug 10:30, same pax. New confirmation #AQMk….

## 5. Cancel booking

**EN**

> Customer: Please cancel the 15 Aug tour.
> Bot: Found it — 15 Aug 10:30, 30 pax. Reply **YES** to cancel (this is irreversible). Any other reply = keep it.
> Customer: YES
> Bot: Cancelled. Confirmation #AQMk…. The deposit refund (if any) will be processed per the original terms.

## 6. Handoff — refund dispute

**EN**

> Customer: I want a refund. The tour guide was rude.
> Bot: I've flagged this for the team — they'll be in touch shortly. For immediate help, contact +65XXXXXXXX.

**中文**

> 客户: 我要退款。导游态度很差。
> 机器人: 已经转给团队处理，他们会尽快联系您。如需紧急协助，请拨打 +65XXXXXXXX。

## 7. Handoff — abuse

**EN**

> Customer: <abusive message>
> Bot: I'm not able to help with that. The team has been notified.
> [admin receives WA message with caller phone + message + `reason=abuse`]
