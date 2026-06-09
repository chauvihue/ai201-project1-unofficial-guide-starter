# Project 1 Planning: The Unofficial Guide

> Write this document before you write any pipeline code.
> Your spec and architecture diagram are what you'll use to direct AI tools (Claude, Copilot, etc.) to generate your implementation — the more specific they are, the more useful the generated code will be.
> Update the Retrieval Approach and Chunking Strategy sections if you change your approach during implementation.
> Update this file before starting any stretch features.

---

## Domain

<!-- What domain did you choose? Why is this knowledge valuable and hard to find through official channels? -->
My domain is Computer Science course selections: what classes to choose for lower electives and such ($\le 300$ level).

As a freshman, I find that asking advisors about which electives to take isn't effecient and useful, since they haven't taken the course yet. There are studnet Peer Advisors, but sometimes you just want a broader view of the general opinion of electives in the recent years. Extracting and combining information directly from Course Descriptions, Reddit and Rate My Professor, we can deploy a RAG system that receives well-rounded, grounded information.

---

## Documents

<!-- List your specific sources: URLs, subreddit names, forum threads, or file descriptions.
     Aim for at least 10 sources that together cover different subtopics or perspectives within your domain. -->

| # | Source | Description | URL or location |
|---|--------|-------------|-----------------|
| 1 | Reddit| Easy 200+ CS Courses |https://www.reddit.com/r/umass/comments/1ot0yto/easy_200_cs_courses/ |
| 2 | Reddit| Course recommendations MS CS |https://www.reddit.com/r/umass/comments/1da5coi/course_recommendations_ms_cs/ |
| 3 | Reddit| Thoughts on certain grad level CS classes |https://www.reddit.com/r/umass/comments/1aojcc8/thoughts_on_certain_grad_level_cs_classes/ |
| 4 | Reddit| Fall 24 CS Grad Course |https://www.reddit.com/r/umass/comments/1bymlyw/fall_24_cs_grad_course/ |
| 5 | Reddit| Easy CS electives |https://www.reddit.com/r/umass/comments/sdcne1/easy_cs_electives/ |
| 6 | Reddit| Easiest cs 400+/500+ courses.... |https://www.reddit.com/r/umass/comments/qubmte/easiest_cs_400500_courses/ |
| 7 | Reddit| CS courseload advice, 300s and 400s|https://www.reddit.com/r/umass/comments/patv3a/cs_courseload_advice/ |
| 8 | Reddit| Freshman CS Major, Second Semester Classes? |https://www.reddit.com/r/umass/comments/jdppi6/freshman_cs_major_second_semester_classes/|
| 9 | Rate My Professor | James Perretta | https://www.ratemyprofessors.com/professor/3114707 |
| 10 | Rate My Professor | Ella Tuson | https://www.ratemyprofessors.com/professor/3127793 |
| 11 | Rate My Professor | Marc Liberatore
 | https://www.ratemyprofessors.com/professor/1948400 |
| 12 | Rate My Professor | Phuthipong Bovornkeeratiroj
 | https://www.ratemyprofessors.com/professor/2992114 |
| 13 | Rate My Professor | Ghazaleh Parvini | https://www.ratemyprofessors.com/professor/2624866 |
| 14 | Rate My Professor | Justin Domke
 | https://www.ratemyprofessors.com/professor/2290260 |
| 15 | Rate My Professor | Marius Minea
 | https://www.ratemyprofessors.com/professor/2416008
| 16 | Rate My Professor | Cole Reilly | https://www.ratemyprofessors.com/professor/2912301 |
| 17 | Rate My Professor | Joe Chiu | https://www.ratemyprofessors.com/professor/2420066 |
| 18 | Rate My Professor | Mordecai Golin | https://www.ratemyprofessors.com/professor/2940693 |
| 19 | Local Repo | Spring 2026 Course Description | documents/s26_course_description  |
| 20 | Local Repo | Spring 2026 Course Schedule | documents/s26_course_schedule |
| 21 | Local Repo | Spring 2026 Eligibility/Prereq. Registration Info | documents/s26_reg_info |
| 22 | Local Repo | Fall 2026 Course Description | documents/f26_course_description |
| 23 | Local Repo | Fall 2026 Course Schedule |  | documents/f26_course_schedule |
| 24 | Local Repo | Fall 2026 Eligibility/Prereq. Registration Info | documents/f26_reg_info |
---

## Chunking Strategy

<!-- How will you split documents into chunks?
     State your chunk size (in tokens or characters), overlap size, and explain why those
     numbers fit the structure of your documents.
     A review-heavy corpus warrants different chunking than a long FAQ. -->

**Chunk size:** Let's try 1000 characters for now

**Overlap:** Let's say 500 characters

**Reasoning:** Course descriptions can be long, so we need bigger chunks. While information may not be dense

---

## Retrieval Approach

<!-- Which embedding model are you using (e.g., all-MiniLM-L6-v2 via sentence-transformers)?
     How many chunks will you retrieve per query (top-k)?
     If you were deploying this for real users and cost wasn't a constraint, what tradeoffs
     would you weigh in choosing a different embedding model — context length, multilingual
     support, accuracy on domain-specific text, latency? -->

**Embedding model:**

**Top-k:**

**Production tradeoff reflection:**

---

## Evaluation Plan

<!-- List your 5 test questions with their expected correct answers.
     Questions should be specific enough that you can judge whether the system's response
     is right or wrong. "What are good dining halls?" is too vague.
     "What do students say about wait times at [dining hall name] during lunch?" is testable. -->

| # | Question | Expected answer |
|---|----------|-----------------|
| 1 | | |
| 2 | | |
| 3 | | |
| 4 | | |
| 5 | | |

---

## Anticipated Challenges

<!-- What could go wrong? Name at least two specific risks with reasoning.
     Consider: noisy or inconsistent documents, missing source attribution, off-topic
     retrieval, chunks that split key information across boundaries. -->

1.

2.

---

## Architecture

<!-- Draw a diagram of your pipeline showing the five stages:
     Document Ingestion → Chunking → Embedding + Vector Store → Retrieval → Generation
     Label each stage with the tool or library you're using.
     You can use ASCII art, a Mermaid diagram, or embed a sketch as an image.
     You'll use this diagram as context when prompting AI tools to implement each stage. -->

---

## AI Tool Plan

<!-- For each part of the pipeline below, describe:
     - Which AI tool you plan to use (Claude, Copilot, ChatGPT, etc.)
     - What you'll give it as input (which sections of this planning.md, which requirements)
     - What you expect it to produce
     - How you'll verify the output matches your spec

     "I'll use AI to help me code" is not a plan.
     "I'll give Claude my Chunking Strategy section and ask it to implement chunk_text()
     with my specified chunk size and overlap" is a plan. -->

**Milestone 3 — Ingestion and chunking:**

**Milestone 4 — Embedding and retrieval:**

**Milestone 5 — Generation and interface:**
