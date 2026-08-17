# KITT UI Packets

Packets are structured status bundles that workers can hand to KITT UI without
requiring a custom screen for every workflow.

The worker produces the packet. KITT UI renders the packet.

## Folders

```text
/Users/erikaflint/code/kitt-ui/packets/active/
/Users/erikaflint/code/kitt-ui/packets/samples/
```

Use `packets/samples/` for contracts and visual examples.

Use `packets/active/` for current runtime packets that should appear as live
operator information.

## Current Packet Types

### calendar_intelligence

Purpose:

- Show what Erika's calendar looks like now.
- Count new consultation bookings as real-world conversions.
- Show near-term openings that can be used in emails, ads, or website buttons.
- Suggest the next practical action for the Fill My Calendar campaign.

Source of truth:

- Acuity for appointments and availability.
- KITT for campaign/job context.

V1 boundary:

- Read-only.
- No appointment changes.
- No emails sent.
- No ad-platform changes.
- No attribution claims.

Sample:

```text
/Users/erikaflint/code/kitt-ui/packets/samples/calendar-intelligence.sample.json
```

## Minimal Shape

```json
{
  "packet_type": "calendar_intelligence",
  "source": "acuity",
  "campaign": "fill_my_calendar",
  "title": "Erika Calendar Snapshot",
  "status": "active",
  "generated_at": "2026-08-17T09:00:00-07:00",
  "summary": {
    "new_consultations": 2,
    "open_consult_slots": 5,
    "next_best_action": "Send Tuesday and Wednesday openings to Meta leads."
  },
  "cards": [],
  "actions": []
}
```

## Design Rule

Packets should say what happened, what is available, and what the next useful
action is. They should not invent facts. If a worker cannot retrieve the data,
it should produce a needs-attention packet instead of guessing.
