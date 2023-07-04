# Tasks

## Model

- [ ] Output quantitative information besides hammering results, for instance "Flip : 0.9" meaning that bitflips occur with 90% probability

## Adapter

- [ ] Handle non-determinism **(Omar)**
  - [ ] Modify synthetic model so that it simulates non-deterministic bit-flips
  - [ ] Implement Sampler component which repeats tests several times and summarises results
- [ ] Have adapter use rowhammer tester to run appropriate tests (e.g., generate JSON files for rowhammer tester)
- [ ] Collect results and translate it back for Learner

## Rowhammer tester

- [ ] Implement tests for triggering bit flips **(Kamil)**
  - [ ] Investigate correct implementation of one-sided/two-sided hammering: do we need to hammer a "distant row" in one-sided so that the current row is closed?
- [ ] Implement tests for detecting TRR
  - See [[1]](#1) and [[2]](#2)
- [ ] Implement tests for detecting ECC
  - on-chip ECC can only be found in DDR5?

## FPGA

- [ ] Implement basic TRR
  - Look at "rowhammer-tester/third_party/litedram/litedram/core/refresher.py" for a refresher module

## Misc/extensions

- [ ] Implement tool to measure retention time and select adjacent rows with similar retention times **(Kamil)**
- [ ] Control temperature and/or represent it in the model

## Scholarship

- [ ] Keep abreast of related  and fast moving literature (maybe have file for collecting this?)
  - [ ] Rowhammer itself
  - [ ] Rowhammer defenses
  - [ ] Active learning
- [ ] Think about potential conferences for publication
- [ ] Think about getting funding for longer range work in this direction

## References

<a id="1">[1] </a> 
Pietro Frigo, Emanuele Vannacci, Hasan Hassan, Victor van der Veen, Onur Mutlu, Cristiano Giuffrida, Herbert Bos, and Kaveh Razavi. (2020). TRRespass: Exploiting the Many Sides of Target Row Refresh. [[pdf]](https://arxiv.org/abs/2004.01807)

<a id="2">[2] </a>
Hassan, H., Tugrul, Y., Kim, J., Veen, V., Razavi, K., & Mutlu, O. (2021). Uncovering In-DRAM RowHammer Protection Mechanisms:A New Methodology, Custom RowHammer Patterns, and Implications. In MICRO-54: 54th Annual IEEE/ACM International Symposium on Microarchitecture (pp. 1198–1213). Association for Computing Machinery. [[pdf]](https://people.inf.ethz.ch/omutlu/pub/U-TRR-uncovering-RowHammer-protection-mechanisms_micro21.pdf)
