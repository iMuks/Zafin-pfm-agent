"""Layer 9 - quality assurance.

Three phases, matching the production eval framework:

    Simulation  -> run the question set against the live agent
    Evaluation  -> a cheap judge model scores every answer on the Big 3
    Analysis    -> a stronger analyst re-reads only the low scores and
                   classifies each as a judge FALSE POSITIVE or a real GAP

The FP/GAP split is why there are two models. A single judge produces a score
you cannot act on: you never know whether a 2/5 means the agent was wrong or
the judge was. Re-reading only the failures is cheap and turns the number into
something you can fix.

Ground truth is computed from the same analytics functions the agent calls, so
"accuracy" means "did it report what the data actually says".
"""
