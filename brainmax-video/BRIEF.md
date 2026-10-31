---
workflow: general-video
flow: companion
storyboard: yes
message: "BrainMax turns an initial learning score into a practical next step."
destination: youtube
aspect: 1920x1080
language: en
audience: "People evaluating or learning to use BrainMax"
length: 4m30s
angle: result-first learner journey
---

## Intent

Create a clear, captioned walkthrough of the BrainMax app, opening on a real baseline result and then rewinding to follow a learner from account setup through assessment, the first learning-path result, and personalized support. Show the real interface and explain how the pieces connect.

## Assets

- BrainMax app at `http://127.0.0.1:8081/` — capture the app's own screens; no extra user assets were provided. This is an isolated demo server and database with a synthetic learner account.

## Customizations

- Include readable English captions for spoken dialogue.
- Show account creation, settings and AI coaching/voice preferences, profile setup, assessment and results, learning path and results, AI Coach, Brain Twin, and Brain/Pinky voice interaction.
- Target a YouTube/website landscape frame (1920×1080); keep the final runtime under five minutes.
- Build together, with the storyboard as a review surface before the composition is built.
- Use offline Kokoro narration. The user selected no music or added media; use the app captures only.
- Selected storyline: “The score finds its next step.” Open on the baseline, rewind to account setup, then follow the Evaluation focus through the first practice result and Brain Twin.

## Notes

- Use the app's actual interface and demonstrable results; do not imply that example or generated scores are validated cognitive measures.
- Keep all capture and render work in this isolated video project, separate from the Django app and its database.
- The learning path is shown in progress at 1/10, with the first activity passed. Do not imply the full path was completed.
- The AI Coach page is captured, but no live OpenRouter chat request has been sent.
