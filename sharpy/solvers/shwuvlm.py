import ctypes as ct
import numpy as np
import scipy.optimize
import scipy.signal

import sharpy.utils.algebra as algebra
import sharpy.aero.utils.uvlmlib as uvlmlib
import sharpy.utils.settings as settings
from sharpy.utils.solver_interface import solver, BaseSolver
import sharpy.utils.generator_interface as gen_interface
import sharpy.utils.cout_utils as cout
import sys


@solver
class SHWUvlm(BaseSolver):
    solver_id = 'SHWUvlm'

    def __init__(self):
        # settings list
        self.settings_types = dict()
        self.settings_default = dict()

        self.settings_types['print_info'] = 'bool'
        self.settings_default['print_info'] = True

        self.settings_types['num_cores'] = 'int'
        self.settings_default['num_cores'] = 0

        # self.settings_types['n_time_steps'] = 'int'
        # self.settings_default['n_time_steps'] = 100

        self.settings_types['convection_scheme'] = 'int'
        self.settings_default['convection_scheme'] = 2

        self.settings_types['dt'] = 'float'
        self.settings_default['dt'] = 0.1

        self.settings_types['iterative_solver'] = 'bool'
        self.settings_default['iterative_solver'] = False

        self.settings_types['iterative_tol'] = 'float'
        self.settings_default['iterative_tol'] = 1e-4

        self.settings_types['iterative_precond'] = 'bool'
        self.settings_default['iterative_precond'] = False

        self.settings_types['velocity_field_generator'] = 'str'
        self.settings_default['velocity_field_generator'] = 'SteadyVelocityField'

        self.settings_types['velocity_field_input'] = 'dict'
        self.settings_default['velocity_field_input'] = {}

        self.settings_types['gamma_dot_filtering'] = 'int'
        self.settings_default['gamma_dot_filtering'] = 0

        self.settings_types['rho'] = 'float'
        self.settings_default['rho'] = 1.225

        self.settings_types['rot_vel'] = 'float'
        self.settings_default['rot_vel'] = 0.0

        self.settings_types['rot_axis'] = 'list(float)'
        self.settings_default['rot_axis'] = np.array([1.,0.,0.])

        self.settings_types['rot_center'] = 'list(float)'
        self.settings_default['rot_center'] = np.array([0.,0.,0.])

        self.data = None
        self.settings = None
        self.velocity_generator = None

    def initialise(self, data, custom_settings=None):
        self.data = data
        if custom_settings is None:
            self.settings = data.settings[self.solver_id]
        else:
            self.settings = custom_settings
        settings.to_custom_types(self.settings, self.settings_types, self.settings_default)

        # self.data.structure.add_unsteady_information(self.data.structure.dyn_dict, self.settings['n_time_steps'].value)

        # init velocity generator
        velocity_generator_type = gen_interface.generator_from_string(
            self.settings['velocity_field_generator'])
        self.velocity_generator = velocity_generator_type()
        self.velocity_generator.initialise(self.settings['velocity_field_input'])

        # Checks
        if not self.settings['convection_scheme'].value == 2:
            sys.exit("ERROR: convection_scheme: %u. Only 2 supported" % self.settings['convection_scheme'].value)

    def run(self,
            aero_tstep=None,
            structure_tstep=None,
            convect_wake=False,
            dt=None,
            t=None,
            unsteady_contribution=False):

        # Checks
        if convect_wake:
            sys.exit("ERROR: convect_wake should be set to False")

        if aero_tstep is None:
            aero_tstep = self.data.aero.timestep_info[-1]
        if structure_tstep is None:
            structure_tstep = self.data.structure.timestep_info[-1]
        if dt is None:
            dt = self.settings['dt'].value
        if t is None:
            t = self.data.ts*dt

        # generate uext
        self.velocity_generator.generate({'zeta': aero_tstep.zeta,
                                          'override': True,
                                          't': t,
                                          'ts': self.data.ts,
                                          'dt': dt,
                                          'for_pos': structure_tstep.for_pos},
                                         aero_tstep.u_ext)
        # if self.settings['convection_scheme'].value > 1 and convect_wake:
        #     # generate uext_star
        #     self.velocity_generator.generate({'zeta': aero_tstep.zeta_star,
        #                                       'override': True,
        #                                       'ts': self.data.ts,
        #                                       'dt': dt,
        #                                       't': t,
        #                                       'for_pos': structure_tstep.for_pos},
        #                                      aero_tstep.u_ext_star)

        # previous_ts = max(len(self.data.aero.timestep_info) - 1, 0) - 1
        # previous_ts = -1
        # print('previous_step max circulation: %f' % previous_aero_tstep.gamma[0].min())
        # print('current step max circulation: %f' % aero_tstep.gamma[0].min())
        uvlmlib.shw_solver(self.data.ts,
                            aero_tstep,
                            structure_tstep,
                            self.settings,
                            convect_wake=False,
                            dt=dt)
        # print('current step max unsforce: %f' % aero_tstep.dynamic_forces[0].max())

        if unsteady_contribution:
            # calculate unsteady (added mass) forces:
            self.data.aero.compute_gamma_dot(dt, aero_tstep, self.data.aero.timestep_info[-3:])
            if self.settings['gamma_dot_filtering'].value > 0:
                self.filter_gamma_dot(aero_tstep, self.data.aero.timestep_info, self.settings['gamma_dot_filtering'].value)
            uvlmlib.uvlm_calculate_unsteady_forces(aero_tstep,
                                                   structure_tstep,
                                                   self.settings,
                                                   convect_wake=convect_wake,
                                                   dt=dt)
        else:
            for i_surf in range(len(aero_tstep.gamma)):
                aero_tstep.gamma_dot[i_surf][:] = 0.0

        return self.data

    def add_step(self):
        self.data.aero.add_timestep()

    def update_grid(self, beam):
        self.data.aero.generate_zeta(beam, self.data.aero.aero_settings, -1, beam_ts=-1)

    def update_custom_grid(self, structure_tstep, aero_tstep):
        self.data.aero.generate_zeta_timestep_info(structure_tstep, aero_tstep, self.data.structure, self.data.aero.aero_settings)

    def update_step(self):
        self.data.aero.generate_zeta(self.data.structure,
                                     self.data.aero.aero_settings,
                                     self.data.ts)

    @staticmethod
    def filter_gamma_dot(tstep, history, filter_param):
        series_length = len(history) + 1
        for i_surf in range(len(tstep.zeta)):
            n_rows, n_cols = tstep.gamma[i_surf].shape
            for i in range(n_rows):
                for j in range(n_cols):
                    series = np.zeros((series_length,))
                    for it in range(series_length - 1):
                        series[it] = history[it].gamma_dot[i_surf][i, j]
                    series[-1] = tstep.gamma_dot[i_surf][i, j]

                    # filter
                    tstep.gamma_dot[i_surf][i, j] = scipy.signal.wiener(series, filter_param)[-1]