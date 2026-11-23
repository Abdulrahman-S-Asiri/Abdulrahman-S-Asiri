# ServeDoctorBench

When a language model server slows down, the first clue may be a longer wait for a response. An engineer still has to work out whether the cause is in the workload, the server, or elsewhere. At Tuwaiq Academy’s Tasar’u AI Infrastructure Bootcamp, our team built ServeDoctorBench to ask whether a model could work from the same evidence as the engineer, identify the fault, and propose a safe next step.

## What the team tested · ماذا اختبر الفريق؟

Our Demo Day poster describes **62 diagnostic cases across 15 fault categories**. The cases span five layers of a vLLM deployment, from the incoming workload through the server, host, proxy, and service lifecycle. In the full-evidence condition (R3), seven models received case evidence drawn from telemetry, logs, configuration, and recent changes.

We asked three questions. Did the model identify the root cause on its first choice? Could it point to supporting evidence? Was its proposed next action safe? Some models proposed safe actions even when they misidentified the cause. Gemini 2.5 Pro judged the responses and was itself among the models evaluated. The poster reports the full-evidence comparison. How the results change when the model receives fewer clues remains an open question. [Read the team’s Demo Day poster (PDF)](Tasaru_A0_ServeDoctorBench.pdf)

## My contribution · دوري في المشروع

I was responsible for project tracking and evaluation and supported the team’s work on vLLM and observability. One concrete part of that work was recording how a healthy server responded before introducing any fault.

I operated `RUN-CLEAN-001` on an **A100-SXM4-80GB** without injecting a fault. For each measured request, I recorded how long the client waited for the first token of the response.

| Healthy baseline | Measurement |
|:---|:---|
| Requests measured | 24, after excluding 3 warm-up requests |
| Mean time to first token | 34 ms |
| Observed range | 19–53 ms |

Those timings describe one healthy run on that setup. Measuring the effect of a fault would require a comparable run with the fault present.

[Back to my profile](../../README.md)
