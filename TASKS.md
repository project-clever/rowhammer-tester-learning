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

