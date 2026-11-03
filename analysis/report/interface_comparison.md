# Review interface comparison

My interface is `analysis/review_app/` (`server.py` and `index.html`). Run it with `.venv/bin/python -u analysis/review_app/server.py` and open http://127.0.0.1:8020/.

## Retained

Margin notes from the reference interface: select the text where the failure shows, write a note beside it, and keep the quote with the note. Tying each open code to the exact words kept my notes about observed behavior rather than general impressions.

## Changed after inspecting traces

Session grouping. In the standard Langfuse view I could not get from a conversation's first turn to its second, because each turn is a separate trace linked only by `cartwheel.session_id`. My interface groups turns into one conversation and treats it as one review item. I also show tool results as inline tables, because I had to expand spans to see output values.

## Remaining limitation

There is no way in the review interface to link notes and labels, this is especially important when the assigned labels differ from the notes when I am doing a second pass.
