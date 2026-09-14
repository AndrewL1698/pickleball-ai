# Project Scope and Product Decisions

## Problem

Recreational pickleball players commonly record matches but extracting meaningful information from those videos requires tedious manual review.

The product attempts to convert ordinary match video into searchable, quantitative match intelligence.

## Target User for MVP

A pickleball player who:

- records full matches from a fixed smartphone
- wants more than a highlight reel
- wants to understand positioning and strategic habits
- is willing to review/correct occasional AI mistakes

## Primary Value Proposition

"Upload a match and discover the patterns behind why you win or lose rallies."

## Competitive Reality

Products such as PB Vision, Picklelytics, and racket-sport tracking applications already validate demand for smartphone-video sports analytics.

The project should therefore not rely on novelty of "AI analyzes pickleball video" as its differentiator.

Potential differentiators:

- transparent confidence scores
- user-correctable predictions
- deeper tactical analytics
- doubles/team-spacing analysis
- evidence-backed insights linked directly to rallies
- strong visual match reconstruction

## Product Principle: Evidence First

A coaching statement should be traceable to statistics and clips.

Bad:

> You struggle with third-shot drops.

Better:

> From the left service court, 7 of your 10 third-shot drops failed to produce a successful kitchen transition. Review rallies 4, 8, 12, and 17.

The user should be able to click the statement and inspect the footage.

## MVP Non-Goals

Do not initially build:

- live coaching
- paddle/swing biomechanics
- social network
- automatic tournament scoring
- rankings
- matchmaking
- smartwatch integration
- multi-camera 3D reconstruction

These distract from the central computer-vision/analytics challenge.

## Success Criteria for Learning Project

The project is successful if it demonstrates:

- a real full-stack application
- asynchronous video processing
- computer-vision inference
- geometric court reconstruction
- temporal tracking
- structured ML outputs
- quantitative evaluation
- data visualization
- a feedback/correction loop

Commercial-grade shot classification is not required for the project to be impressive.
