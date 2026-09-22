// The rover's single instances, defined in scanborn_rover.ino and shared by its modules.
#pragma once

#include "mission.h"
#include "motors.h"
#include "odometry.h"
#include "safety.h"
#include "settings.h"

extern Odometry odometry;
extern Motors motors;
extern Safety safety;
extern Rover robot;
extern RoverSettings settings;
