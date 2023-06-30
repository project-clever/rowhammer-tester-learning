# Tasks

##  Adapter

- [ ] Handle non-determinism
    - Repeat experiments, take average, and cache results?
- [ ] Have adapter use rowhammer tester to run appropriate tests (e.g., generate JSON files for rowhammer tester)
- [ ] Collect results and translate it back for Learner

##  Rowhammer tester

- [ ] Implement tests for triggering bit flips
- [ ] Implement tests for detecting TRR 
- [ ] Implement tests for detecting ECC

##   FPGA

- [ ] Implement basic TRR
    - Look at "rowhammer-tester/third_party/litedram/litedram/core/refresher.py" for a refresher module
     
##   Misc/extensions

- [ ] Implement tool to measure retention and select adjacent rows with similar retention times
- [ ] Control temperature and/or represent it in the model

## Scholarship

- [ ] Keep abreast of related  and fast moving literature (maybe have file for collecting this?)
    - [ ] Rowhammer itself
    - [ ] Rowhammer defenses
    - [ ] Active learning
- [ ] Think about potential conferences for publication
- [ ] Think about getting funding for longer range work in this direction

