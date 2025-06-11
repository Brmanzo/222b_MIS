#!/usr/bin/env python3

import os
import sys
import re
import subprocess
from collections import OrderedDict
from itertools import islice

def locateFeatureIdx(subCircuit, begin, end, substring):
    return next(
        (idx for idx, txt in enumerate(subCircuit[begin:end], start=begin)
        if substring in txt.lstrip()),
        None
    )

def spice_lint(line, gate, subCircuitLines, partial):
    commentPattern = re.compile(r'\bstage(\d+)\b')
    uncommentPattern = re.compile(r'^\*\s*xstage(\d+)\b')

    for idx, line in enumerate(subCircuitLines):
        # Replace default subcircuit path with working path
        if '.include "path_' in line:
            tokens = line.strip().split()
            subCircuitLines[idx] = tokens[0] + ' "' + workingPath.split("/")[0] + "/" + tokens[1].strip('"') + '"\n'
        if(partial):
            # If commented out but current stage, uncomment it
            if uncommentPattern.search(line):
                lineStageNum = int(commentPattern.search(line).group(1))
                if lineStageNum == int(gate.stageNum[-1]):
                    subCircuitLines[idx] = subCircuitLines[idx].strip("*")[1:]
            # If stage instance is beyond the current stage comment it out
            if commentPattern.search(line):
                lineStageNum = int(commentPattern.search(line).group(1))
                if lineStageNum > int(gate.stageNum[-1]) and subCircuitLines[idx][0] != "*":
                    subCircuitLines[idx] = "* " + line
                # Grab true final output net for current stage, not output load
                if lineStageNum == int(gate.stageNum[-1]):
                    stageNOutput = subCircuitLines[idx].split()[-2]
                    stageNInput = subCircuitLines[idx].split()[-3]
        else:
            if commentPattern.search(line):
                lineStageNum = int(commentPattern.search(line).group(1))
                if lineStageNum == int(gate.stageNum[-1]):
                    stageNOutput = subCircuitLines[idx].split()[-2]
                    stageNInput = subCircuitLines[idx].split()[-3]
        # Extend duration of pwl by percentage specified in stageData
        if 'pwl' in line:
            finalPwlLine = subCircuitLines[idx + 3].split()
            finalPwlLine[0] = f"{gate.simTime:+.6e}"
            finalPwlLine[1] += "\n"
            subCircuitLines[idx + 3] = " ".join(finalPwlLine)
        # Append max timestep to the default transient
        if '.tran ' in line and len(subCircuitLines[idx].strip().split()) < 5:
            subCircuitLines[idx] = subCircuitLines[idx].strip() + " 0  1e-12\n"
    if partial:
        # Comment out nets belonging to commented out stages from the .print statement
        for idx, line in enumerate(subCircuitLines):
            if ".print" in line:
                subCircuitLines[idx] = " ".join([subCircuitLines[idx].lstrip("*").strip(), subCircuitLines[idx+1].lstrip("*").strip()]).strip()

                nets = subCircuitLines[idx].strip().split()
                # If there is a net to comment out in the print
                for j, net in enumerate(nets):
                    if gate.netToCommentOut and gate.netToCommentOut in net:
                        nets[j] = "\n*" + net
                        if not getattr(gate, "_extra_print_line", False):
                            gate._extra_print_line = True      # mark done
                        break
                subCircuitLines[idx] = " ".join(nets) + "\n"
                subCircuitLines[idx + 1] = "\n"
    return stageNOutput, stageNInput

def sim_and_read(gate, testLines, testName, delayType, sideInputInstance, l, partial):
    # Write the measure file
    if(l == 1): wcType = "Slowdown"
    elif(l == 0): wcType = "Speedup"
    else: wcType = "Original"

    if delayType == "MIS":
        spice_file = "_".join([workingPath, gate.stageNum, delayType, sideInputInstance, wcType, ".sp"])
        log_file = "_".join([workingPath, gate.stageNum, delayType, sideInputInstance, wcType, ".log"])
    else:
        spice_file = "_".join([workingPath, gate.stageNum, delayType, wcType, ".sp"])
        log_file = "_".join([workingPath, gate.stageNum, delayType, wcType, ".log"])

    with open(spice_file, "w") as f:
        f.writelines(testLines)

    if(verbose):
        print("Measuring " + delayType + " delay at: " + spice_file)
        print("Log file at " + log_file)
    subprocess.run(
        ["ngspice", "-b", "-q", "-o", log_file, spice_file],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    input_delay_pattern = re.compile(r"\b(t_[A-Za-z0-9_]+_delay)_INPUT_(?:rise|fall)\s*=\s*([\d.eE+\-]+)", re.IGNORECASE)
    output_delay_pattern = re.compile(r"\b(t_[A-Za-z0-9_]+_delay)_OUTPUT_(?:rise|fall)\s*=\s*([\d.eE+\-]+)", re.IGNORECASE)

    input_delays = []
    output_delays = []

    with open(log_file) as f:
        for line in f:
            if "Error" in line:
                continue
            m_input = re.match(input_delay_pattern, line)
            if m_input:
                input_delays.append(float(m_input.group(2)))
            m_output = re.match(output_delay_pattern, line)
            if m_output:
                output_delays.append(float(m_output.group(2)))

    input_delay_val = None
    output_delay_val = None

    if input_delays:
        input_delay_val = max(input_delays) # Take the latest arrival time for input
    if output_delays:
        output_delay_val = max(output_delays) # Take the latest arrival time for output

    # Handle cases where no delay was found (e.g., if log was empty or patterns didn't match)
    if input_delay_val is None and not delayType == "MIS":
        print(f"WARNING: Could not find input delay for {log_file}")
        # You might want to assign a very large number or handle as error
        input_delay_val = 0.0 # Or some default that makes sense for your flow
    if output_delay_val is None:
        print(f"WARNING: Could not find output delay for {log_file}")
        output_delay_val = 0.0 # Or some default
        
    if(partial):
        return output_delay_val, input_delay_val
    else:
        return output_delay_val

def measure_sis_delay(gate, measureLines, line, lines, partial, first, l):
    testName = "t_" + workingPath.split("/")[1] + "_sis_delay"
    if(first):
        measureLines.append(lines[0])
        measureLines.append(".option noaskquit\n")
        measureLines.append(models)
        measureLines.append(cellLibrary)
        if(partial):
            measureLines.extend(lines[2: gate.endsLine + 1])
            stageNOutput, stageNInput = spice_lint(line, gate, measureLines, partial)
        else: # SIS delay for total critical path
            measureLines.extend(lines[2:-1])
            stageNOutput, stageNInput = spice_lint(line, gate, measureLines, partial)
            stageNOutput = gate.finalOutput
    else:
        stageNOutput, stageNInput = spice_lint(line, gate, measureLines, partial)


    if gate.stage1Vi > 0:
        measureLines.append(".measure tran " + testName + "_INPUT_FALL TRIG v(" + gate.stage1Input + ") VAL="+  str(float(gate.VDD)/2) + " FALL=1 TARG v(" + stageNInput + ") VAL="+  str(float(gate.VDD)/2) + " FALL=1\n")
        measureLines.append(".measure tran " + testName + "_INPUT_RISE TRIG v(" + gate.stage1Input + ") VAL="+  str(float(gate.VDD)/2) + " FALL=1 TARG v(" + stageNInput + ") VAL="+  str(float(gate.VDD)/2) + " RISE=1\n")
        measureLines.append(".measure tran " + testName + "_OUTPUT_FALL TRIG v(" + gate.stage1Input + ") VAL="+  str(float(gate.VDD)/2) + " FALL=1 TARG v(" + stageNOutput + ") VAL="+  str(float(gate.VDD)/2) + " FALL=1\n")
        measureLines.append(".measure tran " + testName + "_OUTPUT_RISE TRIG v(" + gate.stage1Input + ") VAL="+  str(float(gate.VDD)/2) + " FALL=1 TARG v(" + stageNOutput + ") VAL="+  str(float(gate.VDD)/2) + " RISE=1\n")
        measureLines.append(".plot tran v(" + gate.stage1Input + ") v(" + stageNOutput + ")\n.end\n")
    else:
        measureLines.append(".measure tran " + testName + "_INPUT_FALL TRIG v(" + gate.stage1Input + ") VAL="+  str(float(gate.VDD)/2) + " RISE=1 TARG v(" + stageNInput + ") VAL="+  str(float(gate.VDD)/2) + " FALL=1\n")
        measureLines.append(".measure tran " + testName + "_INPUT_RISE TRIG v(" + gate.stage1Input + ") VAL="+  str(float(gate.VDD)/2) + " RISE=1 TARG v(" + stageNInput + ") VAL="+  str(float(gate.VDD)/2) + " RISE=1\n")
        measureLines.append(".measure tran " + testName + "_OUTPUT_FALL TRIG v(" + gate.stage1Input + ") VAL="+  str(float(gate.VDD)/2) + " RISE=1 TARG v(" + stageNOutput + ") VAL="+  str(float(gate.VDD)/2) + " FALL=1\n")
        measureLines.append(".measure tran " + testName + "_OUTPUT_RISE TRIG v(" + gate.stage1Input + ") VAL="+  str(float(gate.VDD)/2) + " RISE=1 TARG v(" + stageNOutput + ") VAL="+  str(float(gate.VDD)/2) + " RISE=1\n")
        measureLines.append(".plot tran v(" + gate.stage1Input + ") v(" + stageNOutput + ")\n.end\n")

    if partial:
        output_delay_val, input_delay_val = sim_and_read(gate, measureLines, testName, "SIS", None, l, partial)
        return output_delay_val, input_delay_val, stageNOutput
    else:
        final_output_delay_val = sim_and_read(gate, measureLines, testName, "SIS", None, l, partial)
        return final_output_delay_val

def measure_mis_delay(subCircuitLines, finalV, instanceName, mainInputAT, gate, line, lines, instanceLine, instanceIdx, l):
    # If first mis, construct entire file
    firstWorstImpactIdx = next(
        (i for i, stage in enumerate(gates) if (stage.worstSpeedupImpact or stage.worstSlowdownImpact)),
        None
    )
    # If previous stage had side inputs and generated a MIS, extend file with next stage from lines
    if (sum(1 for stage in gates if stage.worstSpeedupImpact) > 0 or sum(1 for stage in gates if stage.worstSlowdownImpact) > 0) and \
        (int(gate.stageNum[-1]) > int(gates[firstWorstImpactIdx].stageNum[-1])):
        subCircuitLines[-1] = "\n"
        subCircuitLines.extend(lines[gate.gateLine - 1: gate.endsLine + 1])
    # with open("test_SCL.sp", "w") as f:
    #     f.writelines(subCircuitLines)
    # print("Written to test_SCL.sp")
    # Rising or Falling pwl

    if(float(finalV) > 0):
        subCircuitLines[locateFeatureIdx(subCircuitLines, gate.gateLine, gate.endsLine, instanceName)] = instanceName + " PWL(0ns 0V " + str(mainInputAT) + " " + str(gate.VDD) +  "V)\n"
    else:
        subCircuitLines[locateFeatureIdx(subCircuitLines, gate.gateLine, gate.endsLine, instanceName)] = instanceName + " PWL(0ns " +  str(gate.VDD) + "V "+ str(mainInputAT) + " 0V)\n"
    # After modifying side input instance, append the rest of the subcircuit
    #subCircuitLines.extend(lines[instanceIdx + 1 : gate.endsLine + 1])

    sideInputInstance = instanceLine[1].replace('/', '')
    testName = "t_mis" + sideInputInstance.lower() + "_delay"
    # add trans to testbench following the testbench
    if gate.stage1Vi > 0:
        subCircuitLines.append(".measure tran " + testName + "_OUTPUT_FALL TRIG v(" + gate.stage1Input + ") VAL="+  str(float(gate.VDD)/2) + " FALL=1 TARG v(" + stageNOutput + ") VAL="+  str(float(gate.VDD)/2) + " FALL=1\n")
        subCircuitLines.append(".measure tran " + testName + "_OUTPUT_RISE TRIG v(" + gate.stage1Input + ") VAL="+  str(float(gate.VDD)/2) + " FALL=1 TARG v(" + stageNOutput + ") VAL="+  str(float(gate.VDD)/2) + " RISE=1\n")
        subCircuitLines.append(".plot tran v(" + gate.stage1Input + ") v(" + instanceName + ") v(" + stageNOutput + ")\n.end\n")

    else:
        subCircuitLines.append(".measure tran " + testName + "_OUTPUT_FALL TRIG v(" + gate.stage1Input + ") VAL="+  str(float(gate.VDD)/2) + " RISE=1 TARG v(" + stageNOutput + ") VAL="+  str(float(gate.VDD)/2) + " FALL=1\n")
        subCircuitLines.append(".measure tran " + testName + "_OUTPUT_RISE TRIG v(" + gate.stage1Input + ") VAL="+  str(float(gate.VDD)/2) + " RISE=1 TARG v(" + stageNOutput + ") VAL="+  str(float(gate.VDD)/2) + " RISE=1\n")
        subCircuitLines.append(".plot tran v(" + gate.stage1Input + ") v(" + instanceName + ") v(" + stageNOutput + ")\n.end\n")

    # Replace includes with proper paths
    spice_lint(line, gate, subCircuitLines, True)

    # Write subcircuit sim to separate file
    # subcktFile: path#_stage#_sim
    return sim_and_read(gate, subCircuitLines, (testName), "MIS", sideInputInstance, l, False)


class stageData:
    def locateFeatureIdx(self, begin, end, substring):
        return next(
            (idx for idx, txt in enumerate(lines[begin:end], start=begin)
            if substring in txt.lstrip()),
            None
        )

    def __init__(self, lines, gateIdx):
        self.criticalNets = criticalDefaults[:]
        self.sideInputs   = []
        
        # Lines for navigating Gate
        self.gateLine = gateIdx
        self.netListLine = self.gateLine + 1
        
        self.beginNetInstances = self.gateLine + 2
        self.endNetInstances = self.locateFeatureIdx(self.netListLine, None, "* Load pins") - 2
        
        self.endsLine = self.locateFeatureIdx(self.gateLine, None, ".ends")

        self._extra_print_line = False

        # Critical Net Data
        # Record the subcircuit input and output nets
        critPath = lines[self.gateLine].split(" ")
        self.criticalNets.append("/" + critPath[critPath.index("->") - 1])
        self.criticalNets.append("/" + critPath[critPath.index("->") + 1].strip())

        # Add full instance name for input net from netlist line 
        for net in lines[self.netListLine].strip().split():
            for idx, critical in enumerate(self.criticalNets):
                if critical in net:
                    # replace the old entry at index `idx` with the new `net`
                    self.criticalNets[idx] = net
                    break

        self.nets = lines[self.netListLine].strip().split()[1:-1]
        # Add Side input information to dictionary
        for net in self.nets:
            if all(crit not in net for crit in self.criticalNets):
                if net not in self.sideInputs:
                    self.sideInputs.append(net)
                    simData[net] = [net]

        # Stage Simulation Data
        self.simTimePct = 1.1
        self.simTime = float(lines[self.locateFeatureIdx(0, None, "pwl") + 3].split()[0]) * self.simTimePct
        self.stage1Input = lines[self.locateFeatureIdx(0, None, "xstage1")].strip().split()[1]
        self.stage1Vi = float(lines[self.locateFeatureIdx(0, None, "pwl") + 1].strip().split()[-1])
        self.stageNum = lines[self.gateLine - 1].split(" ")[1]

        self.finalOutput = lines[self.locateFeatureIdx(0, None, ".print")].strip().split()[-2].strip("v()")

        nextStageNum = self.locateFeatureIdx(0, None,
                                       "stage" + str(int(self.stageNum[-1]) + 1))
        if nextStageNum is not None:
            self.netToCommentOut = lines[nextStageNum].split()[2]
        else:
            self.netToCommentOut = ""

        # Initialize worst case
        self.worstSpeedupImpact  = None
        self.worstSlowdownImpact = None
        self.wcSpeedupInstance   = None
        self.wcSlowdownInstance  = None
        self.wcAccSlowdown       = None
        self.wcAccSpeedup        = None

        self.VDD = lines[self.locateFeatureIdx(self.beginNetInstances+1, None, "/VPWR")].split()[-1]
        self.GND = lines[self.locateFeatureIdx(self.beginNetInstances+1, None, "/VGND")].split()[-1]

        
# Nets to ignore
criticalDefaults = ["/VGND", "/VNB", "/VPB", "/VPWR"]
gates = []
simData      = OrderedDict()

subcktFile = sys.argv[1]
models = '.lib "' + str(os.path.expanduser("~")) + '/.volare/sky130A/libs.tech/ngspice/sky130.lib.spice" tt\n'
cellLibrary = '.include "' + str(os.path.expanduser("~")) + '/.volare/sky130A/libs.ref/sky130_fd_sc_hd/spice/sky130_fd_sc_hd.spice"\n'
includes = {".option noaskquit\n", models, cellLibrary}
workingPath = sys.argv[1].split(".")[0]

verbose = False

# Clean Method
if "--clean" in sys.argv:
    subprocess.run("rm subcircuits/*IS*", shell=True, check=True)
    sys.exit()

# verbose mode
if "--v" in sys.argv:
    verbose = True

if "--so" in sys.argv:
    # Find the index of '--so'
    so_index = sys.argv.index('--so')
    # Check if there's an element after '--so'
    if so_index + 1 < len(sys.argv):
        # Try to convert it to a number
        number_str = sys.argv[so_index + 1]
        startingOffset = float(number_str) # Or float(number_str)
        print(str("Initial time: " + str(startingOffset)))
    else:
        print("Error: --so token found, but no number followed.")
        sys.exit(1)

# Otherwise take spice file from command line
with open(subcktFile, 'r') as data_file:
    lines = data_file.readlines()

i = 0
wcSpeedupLines = []
wcSlowdownLines = []
worstCases = []
worstCases.extend([wcSpeedupLines, wcSlowdownLines])
mainInputATs  = [None, None]
mainOutputATs = [None, None]
measureLines = []
originalAT = None
# Iterate over each line of the subcircuit
while i < len(lines):
    line = lines[i].strip()

    if line.startswith("* Gate"):
        # Characterize Gate Information
        gate = stageData(lines, i)
        gates.append(gate)
        # initialize spice file lines for new gate
        subCircuitLines   = []
        measureLines  = [] 

        # Measure SIS delay from start of stage 1, to end of current stage
        if gate.sideInputs:
            print(gate.stageNum)
            # If first Side input stage, perform initial SIS measurement
            if (sum(1 for stage in gates if stage.worstSpeedupImpact) < 1 or sum(1 for stage in gates if stage.worstSlowdownImpact) < 1) and len(measureLines) == 0 :
                # Measure total SIS output delay
                originalAT  = measure_sis_delay(gate, measureLines, line, lines, False, True, None)
                print(f"SIS Delay at last stage: {originalAT * 1e12:.3f} ps")
                measureLines  = []
                mainOutputAT, mainInputAT, stageNOutput = measure_sis_delay(gate, measureLines, line, lines, True, True, None)
                print(f"SIS Delay to main input: {mainInputAT * 1e12:.3f} ps")
                print(f"SIS Delay to gate output: {mainOutputAT * 1e12:.3f} ps")
                for l in range(len(worstCases)):
                    worstCases[l]   = measureLines
                    mainInputATs[l]  = mainInputAT
                    mainOutputATs[l] = mainOutputAT
                
            else:
                for l in range(len(worstCases)):
                    measureLines = []
                    measureLines = list(worstCases[l][:locateFeatureIdx(worstCases[l], 0, None, ".measure") + 1])
                    measureLines[-1] = "\n"
                    measureLines.extend(lines[gate.gateLine - 1: gate.endsLine + 1])

                    mainOutputATs[l], mainInputATs[l], stageNOutput = measure_sis_delay(gate, measureLines, line, lines, True, False, l)
                    print(f"SIS Delay to main input: {mainInputATs[l] * 1e12:.3f} ps")
                    print(f"SIS Delay to gate output: {mainOutputATs[l] * 1e12:.3f} ps")



            # Advance to instances
            i = gate.beginNetInstances

            # 0 is Speedup, 1 is Slowdown
            for l in range(len(worstCases)):
                # For all side inputs in instances, measure the MIS delay
                for j in range(gate.beginNetInstances, gate.endNetInstances + 1):
                    instanceLine = lines[j].strip().split()

                    if not instanceLine:
                        continue
                    if instanceLine[0].lower().startswith('v') and instanceLine[1] in gate.sideInputs:
                        instanceName = " ".join(instanceLine[:-1])
                        finalV       = instanceLine[-1]


                        # Append voltage data to dictionary
                        simData[instanceLine[1]].extend(instanceLine[-2:])
                        # Grab entire circuit up to
                        subCircuitLines = list(worstCases[l][:locateFeatureIdx(worstCases[l], 0, None, ".measure") + 1])
                        

                        misDelay = measure_mis_delay(subCircuitLines, finalV, instanceName, mainInputATs[l], gate, line, lines, instanceLine, j, l)
                        
                        
                        impact = float(misDelay) - float(mainOutputATs[l])
                        simData[instanceLine[1]].append(impact)
                        print("MIS_Impact at " + instanceLine[1] + ": " + f"{impact * 1e12:.3f} ps")

                        # track per-gate worst case on the fly
                        impact  = misDelay - mainOutputATs[l]        # signed Δ-delay for this side-input

                        # ----- keep the two branches independent -----
                        if l == 0:                # SPEED-UP branch (we want the most-negative impact)
                            if impact < 0:
                                if (gate.worstSpeedupImpact is None) or (impact < gate.worstSpeedupImpact):
                                    gate.worstSpeedupImpact = impact
                                    gate.wcSpeedupInstance  = instanceLine[1].replace('/', '')
                                    gate.wcAccSpeedup       = misDelay

                        else:                      # SLOW-DOWN branch (we want the most-positive impact)
                            if impact > 0:
                                if (gate.worstSlowdownImpact is None) or (impact > gate.worstSlowdownImpact):
                                    gate.worstSlowdownImpact = impact
                                    gate.wcSlowdownInstance  = instanceLine[1].replace('/', '')
                                    gate.wcAccSlowdown       = misDelay

        if gate.worstSpeedupImpact is None:
            gate.wcAccSpeedup = mainOutputATs[0]
        if gate.worstSlowdownImpact is None:
            gate.wcAccSlowdown = mainOutputATs[1]
        
        i = gate.endsLine + 1
        # Remember worst case spice file and append new stage to it next
        if gate.wcSpeedupInstance:
            with open("_".join([workingPath, gate.stageNum, "MIS", gate.wcSpeedupInstance, "Speedup", ".sp"]), 'r') as wcFile:
                worstCases[0] = wcFile.readlines()
        else:
            pass
        if gate.wcSlowdownInstance:
            with open("_".join([workingPath, gate.stageNum, "MIS", gate.wcSlowdownInstance, "Slowdown", ".sp"]), 'r') as wcFile:
                worstCases[1] = wcFile.readlines()
        else:
            pass
    else:
        i += 1
if gates[-1].wcAccSpeedup > 0 or gates[-1].wcAccSlowdown < 0:

    finalSpeedup = (originalAT - gates[-1].wcAccSpeedup) * 1e12
    finalSlowdown = (gates[-1].wcAccSlowdown - originalAT) * 1e12

    print(f"Worst Case Speed-up: {finalSpeedup:.3f} ps")
    print("When ", end='')
    for gate in gates:
        if gate.wcSpeedupInstance:
            print(gate.wcSpeedupInstance + ", ", end='')
    print("switch with critical path")

    print(f"Worst Case Slow-down: {finalSlowdown:.3f} ps")
    print("When ", end='')
    for gate in gates:
        if gate.wcSlowdownInstance:
            print(gate.wcSlowdownInstance + ", ", end='')
    print("switch with critical path")
    print(f"AT Window: {gates[-1].wcAccSpeedup * 1e9 + startingOffset:.3f} - {gates[-1].wcAccSlowdown * 1e9 + startingOffset:.3f} ns")


    # for lst in list(simData.values()):
    #     print(lst)

    # for gate in gates:
    #     if gate.worstImpact:
    #         print(gate.stageNum + ": " + str(gate.worstImpact))
else:
    print("No side inputs in path")

