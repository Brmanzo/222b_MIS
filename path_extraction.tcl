read_lib $env(PDK_ROOT)/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__ss_100C_1v60.lib
read_db odb/spm.odb 
read_spef spef/max/spm.max.spef
read_sdc sdc/spm.sdc

file mkdir tmp_output
set summaryFile [open tmp_output/arrival_windows.txt "w"]

set max_paths 1000

for {set i 0} {$i < $max_paths} {incr i} {
    set path [lindex [find_timing_paths -sort_by_slack -group_count [expr {$i+1}]] $i]

    if {[llength $path] == 0} {
        puts "No more paths after index $i, quitting."
        break
    }

    set slack [get_property $path slack]
    set startpoint [get_property [get_property $path startPoint] full_name]
    set endpoint [get_property [get_property $path endPoint] full_name]

    puts "Processing path $i : Slack $slack | From $startpoint | To $endpoint"

    set path_args [list -from $startpoint -to $endpoint]

    sta::write_path_spice \
        -path_args $path_args \
        -spice_directory "tmp" \
        -lib_subckt_file "$env(PDK_ROOT)/sky130A/libs.ref/sky130_fd_sc_hd/cdl/sky130_fd_sc_hd.cdl" \
        -model_file "$env(PDK_ROOT)/sky130A/libs.tech/ngspice/sky130.lib.spice" \
        -simulator ngspice \
        -power VPWR \
        -ground VGND

    file rename -force tmp/path_1.sp tmp_output/path_${i}.sp
    file rename -force tmp/path_1.subckt tmp_output/path_${i}.subckt

    set path [lindex [find_timing_paths -path_delay max -sort_by_slack -group_count [expr {$i+1}]] $i]

    set arcFile [open tmp_output/path_${i}_arcs.txt "w"]
    puts $arcFile "Timing Arcs for Min Delay Path $i"
    puts $arcFile "Slack: $slack"
    puts $arcFile "Startpoint: $startpoint"
    puts $arcFile "Endpoint: $endpoint\n"

    puts $summaryFile "Path $i:"

    set pathrefs [get_property $path points]

    for {set j 0} {$j < [expr {[llength $pathrefs] - 1}]} {incr j} {
        set pathrefA [lindex $pathrefs $j]
        set pathrefB [lindex $pathrefs [expr {$j + 1}]]

        set pinA [get_property $pathrefA pin]
        set pinB [get_property $pathrefB pin]
        set clk [all_clocks]

        set arrival [report_arrival $pinA]
        set vertex [$pinA vertices]
        set arrival [$vertex arrivals_clk_delays rise $clk "rise" 6]

        puts $arcFile "\nPoint: [get_property $pinA full_name]"
        puts $arcFile "  Arrival Time: $arrival"

        puts $summaryFile "  Point: [get_property $pinA full_name]"
        puts $summaryFile "    Arrival Time: $arrival"

        set arcs [get_timing_edges -from $pinA -to $pinB]
        foreach arc $arcs {
            set from [get_property $arc from_pin]
            set to   [get_property $arc to_pin]
            set from_name [get_property $from full_name]
            set to_name   [get_property $to full_name]
            set sense     [get_property $arc sense]
            set d_max_r   [get_property $arc delay_max_rise]
            set d_max_f   [get_property $arc delay_max_fall]

            puts $arcFile "  Arc: $from_name → $to_name | Sense: $sense | MaxRise: $d_max_r | MaxFall: $d_max_f"
        }
    }

    set lastref [lindex $pathrefs end]
    set last_pin [get_property $lastref pin]
    set final_arrival [get_property $lastref arrival]

    puts $arcFile "\nFinal Point: [get_property $last_pin full_name]"
    puts $arcFile "  Arrival Time: $final_arrival"

    puts $summaryFile "  Final Point: [get_property $last_pin full_name]"
    puts $summaryFile "    Arrival Time: $final_arrival\n"

    close $arcFile
}

close $summaryFile
